import csv
import io
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.responses import JSONResponse, Response

from app.schemas.jobs import JobFilters, JobPatch, ManualJobCreate, trusted_source_for_url
from app.schemas.saved_views import SavedViewCreate, SavedViewPatch
from app.services.refresh import RefreshConflict

router = APIRouter(prefix="/api")

from app.services.gmail import GmailError
RecordId = Annotated[int, Path(ge=1, le=9_223_372_036_854_775_807)]


def database(request: Request):
    return request.app.state.db


def gmail_error(exc: GmailError):
    status = 409 if exc.code == "gmail_check_running" else 400
    raise HTTPException(status, detail={"code": exc.code, "message": str(exc)}) from None


def not_found(kind: str):
    raise HTTPException(404, detail={"code": "not_found", "message": f"{kind} not found."})


def run_response(run):
    return {**run, "state": run["status"]}


def csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, dict)):
        value = "; ".join(str(item) for item in value) if isinstance(value, list) else json.dumps(value, ensure_ascii=False)
    text = str(value)
    # Prevent spreadsheet programs from interpreting private text as formulas.
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


@router.get("/health")
def health(request: Request, db=Depends(database)):
    try:
        healthy = db.health()
    except Exception:
        healthy = False
    scheduler = request.app.state.scheduler
    job = scheduler.get_job("daily_job_refresh") if scheduler else None
    next_run = getattr(job, "next_run_time", None)
    return JSONResponse(status_code=200 if healthy else 503, content={
        "status": "ok" if healthy else "error", "database": "ok" if healthy else "unavailable",
        "timezone": "Asia/Manila", "scheduler_running": bool(scheduler and scheduler.running),
        "next_refresh_at": next_run.isoformat() if next_run else None,
        "active_refresh_run_id": request.app.state.refresh.active_run_id,
        "milestone": 1,
    })


@router.get("/jobs")
def jobs(filters: Annotated[JobFilters, Query()], db=Depends(database)):
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise HTTPException(422, detail={"code": "invalid_date_range", "message": "date_from must not be after date_to."})
    return db.list_jobs(filters.model_dump(exclude_none=True))


@router.get("/jobs/{job_id}")
def job_detail(job_id: RecordId, db=Depends(database)):
    job = db.get_job(job_id)
    if job is None:
        not_found("Job")
    return job


@router.patch("/jobs/{job_id}")
def patch_job(job_id: RecordId, patch: JobPatch, db=Depends(database)):
    result = db.patch_job(job_id, **patch.model_dump(exclude_unset=True))
    if result is None:
        not_found("Job")
    return result


@router.post("/jobs/manual", status_code=201)
def create_manual_job(payload: ManualJobCreate, db=Depends(database)):
    source = trusted_source_for_url(payload.source_url)
    # The schema already validates this. Keep the route safe if the schema changes.
    if source is None:
        raise HTTPException(422, detail={"code": "unsupported_source", "message": "Use a trusted job-board listing URL."})
    result = db.create_manual_job(source=source, **payload.model_dump())
    if result is None:
        raise HTTPException(409, detail={"code": "duplicate_job", "message": "This job is already saved. Its status and notes were not changed."})
    return result


@router.post("/refresh", status_code=202)
async def refresh(request: Request):
    service = request.app.state.refresh
    try:
        run_id = await service.start("manual")
    except RefreshConflict as exc:
        raise HTTPException(409, detail={"code": "refresh_in_progress", "message": "A search is already running.", "run_id": exc.run_id}) from None
    return {"run_id": run_id, "state": "running", "status_url": f"/api/refresh/{run_id}"}


@router.get("/refresh/{run_id}")
def refresh_status(run_id: RecordId, db=Depends(database)):
    run = db.get_sync_run(run_id)
    if run is None:
        not_found("Refresh run")
    return run_response(run)


@router.get("/sync-runs")
def sync_runs(limit: Annotated[int, Query(ge=1, le=100)] = 20, db=Depends(database)):
    return {"items": [run_response(run) for run in db.list_sync_runs(limit)]}


@router.get("/saved-views")
def saved_views(db=Depends(database)):
    return {"items": db.list_saved_views()}


@router.post("/saved-views", status_code=201)
def create_saved_view(payload: SavedViewCreate, db=Depends(database)):
    try:
        return db.create_saved_view(payload.name, payload.filters.model_dump(exclude_none=True), payload.sort_by, payload.sort_order)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "saved_view_conflict", "message": str(exc)}) from None


@router.patch("/saved-views/{view_id}")
def patch_saved_view(view_id: RecordId, payload: SavedViewPatch, db=Depends(database)):
    changes = payload.model_dump(exclude_unset=True)
    if "filters" in changes:
        changes["filters"] = payload.filters.model_dump(exclude_none=True)
    try:
        result = db.update_saved_view(view_id, **changes)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "saved_view_conflict", "message": str(exc)}) from None
    if result is None:
        not_found("Saved view")
    return result


@router.delete("/saved-views/{view_id}", status_code=204)
def delete_saved_view(view_id: RecordId, db=Depends(database)):
    if not db.delete_saved_view(view_id):
        not_found("Saved view")
    return Response(status_code=204)


@router.get("/export/jobs.csv")
def export_jobs(filters: Annotated[JobFilters, Query()], db=Depends(database)):
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise HTTPException(422, detail={"code": "invalid_date_range", "message": "date_from must not be after date_to."})
    rows = db.export_jobs(filters.model_dump(exclude_none=True, exclude={"page", "page_size"}))
    columns = [
        ("Title", "title"), ("Company", "company"), ("Location", "location"),
        ("Source", "source"), ("URL", "source_url"), ("Posted at", "posted_at"),
        ("Date found", "date_found"), ("First seen", "first_seen_at"), ("Last seen", "last_seen_at"),
        ("Match reasons", "match_reasons"), ("Salary minimum", "salary_min"),
        ("Salary maximum", "salary_max"), ("Salary currency", "salary_currency"),
        ("Salary period", "salary_period"), ("Work arrangement", "work_arrangement"),
        ("Employment type", "employment_type"), ("Preferred company", "preferred_company"),
        ("Stale", "is_stale"), ("Status", "status"), ("Notes", "notes"),
    ]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([label for label, _ in columns])
    for row in rows:
        writer.writerow([csv_cell(row.get(field)) for _, field in columns])
    filename = datetime.now(timezone.utc).strftime("jobs_%Y%m%dT%H%M%SZ.csv")
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/backup", status_code=201)
def backup_database(request: Request, db=Depends(database)):
    try:
        path = db.backup(request.app.state.settings.backup_dir)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, detail={"code": "backup_failed", "message": str(exc)}) from None
    return {"filename": path.name, "path": str(path), "created_at": datetime.now(timezone.utc).isoformat(),
            "message": "Local database backup created. Keep this file private."}


@router.get("/gmail/status")
def gmail_status(request: Request):
    return request.app.state.gmail.status()


@router.get("/gmail/auth/start")
def gmail_auth_start(request: Request):
    try:
        return RedirectResponse(request.app.state.gmail.authorization_url(), status_code=302)
    except GmailError as exc:
        gmail_error(exc)


@router.get("/gmail/auth/callback")
async def gmail_auth_callback(request: Request, state: str = Query(min_length=20, max_length=200),
                              code: str | None = Query(default=None, max_length=4096),
                              error: str | None = Query(default=None, max_length=200)):
    frontend = request.app.state.settings.cors_origins[0]
    if error or not code:
        return RedirectResponse(frontend + "/?gmail=error", status_code=302)
    try:
        await request.app.state.gmail.complete_authorization(state, code)
    except GmailError:
        return RedirectResponse(frontend + "/?gmail=error", status_code=302)
    return RedirectResponse(frontend + "/?gmail=connected", status_code=302)


@router.post("/gmail/check")
async def gmail_check(request: Request):
    try:
        return await request.app.state.gmail.check()
    except GmailError as exc:
        gmail_error(exc)


@router.get("/gmail/suggestions")
def gmail_suggestions(state: str | None = Query(default=None, pattern="^(pending|confirmed|dismissed)$"),
                      linked: bool | None = None, db=Depends(database)):
    return {"items": db.list_gmail_suggestions(state, linked)}


@router.post("/gmail/suggestions/{suggestion_id}/confirm")
def confirm_gmail_suggestion(suggestion_id: RecordId, db=Depends(database)):
    result = db.confirm_gmail_suggestion(suggestion_id)
    if result is None:
        raise HTTPException(409, detail={"code": "suggestion_not_confirmable",
            "message": "This suggestion is missing a linked job or has already been resolved."})
    return result


@router.post("/gmail/suggestions/{suggestion_id}/dismiss")
def dismiss_gmail_suggestion(suggestion_id: RecordId, db=Depends(database)):
    result = db.dismiss_gmail_suggestion(suggestion_id)
    if result is None:
        raise HTTPException(409, detail={"code": "suggestion_not_pending",
            "message": "This suggestion has already been resolved."})
    return result
