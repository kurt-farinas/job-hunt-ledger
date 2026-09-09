# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is one junior frontend/full-stack developer in the Philippines seeking roles in Metro Manila and remote. They use the dashboard locally to discover suitable jobs, understand why each role matched, and manually track applications over days or weeks.

## Product Purpose

The dashboard turns approved public job feeds into a private, explainable working list. Success means the user can quickly find credible junior roles, avoid duplicate review, keep application notes and status history accurate, and retain full control over every application and status change.

## Positioning

It combines deterministic, readable job-match reasons with a local application tracker. The same bounded ingestion pipeline powers manual and daily searches while preserving the user’s notes and decisions.

## Operating Context

The app runs as separate FastAPI and Vite development servers on the user’s computer. The user scans fresh results, filters and sorts them, opens official job links in a new tab, records notes and statuses after acting elsewhere, saves useful views, exports filtered results, and creates local database backups. Asia/Manila is the displayed and scheduling timezone.

## Capabilities and Constraints

The app uses React, Vite, TypeScript, FastAPI, SQLite, HTTPX, and APScheduler. It is local-only and accepts data from approved APIs or feeds. It never applies, sends email, automates a browser for extraction, changes Gmail, or automatically changes a job status. Status and note writes are explicit user actions. The dashboard must support keyboard use, responsive layouts, clear focus, adequate contrast, and screen-reader announcements.

Milestone 3 adds RemoteOK, We Work Remotely, Greenhouse, and Lever to the shared ingestion pipeline. Gmail suggestions remain a later milestone.

## Evidence on Hand

The repository contains the tested Milestone 1 backend, synthetic test fixtures, API contracts, and editable source and preference configuration. It contains no real job listings, private notes, user testimonials, performance claims, or brand assets; future work must not fabricate those.

## Product Principles

- Keep private data on the user’s computer.
- Explain every match with readable evidence.
- Make refreshes repeatable and preserve manual decisions.
- Keep status changes and notes deliberate and reversible through visible controls.
- Optimize the first screen for scanning new opportunities without hiding tracking context.

## Accessibility & Inclusion

All primary workflows must be keyboard operable, use visible labels and focus states, announce asynchronous results, and communicate status with text in addition to color. Layouts must remain usable on narrow screens without forcing a desktop-only interaction model.
