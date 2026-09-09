---
name: Job Hunt Ledger
description: A field-survey ledger for private, explainable job hunting.
colors:
  survey-ink: "#102b3f"
  survey-ink-soft: "#29485e"
  paper: "#f8faf9"
  paper-deep: "#edf1f1"
  canvas: "#e8edf0"
  rule: "#c7d2d8"
  rule-dark: "#8da0ac"
  field-amber: "#f2b84b"
  link-blue: "#0f526b"
  danger: "#a63838"
  success: "#2c6e56"
typography:
  display:
    fontFamily: "Segoe UI Variable, Aptos, Segoe UI, system-ui, sans-serif"
    fontSize: "clamp(26px, 3vw, 42px)"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Segoe UI Variable, Aptos, Segoe UI, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 800
    lineHeight: 1.25
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Segoe UI Variable, Aptos, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Segoe UI Variable, Aptos, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 750
    lineHeight: 1.25
    letterSpacing: "0.06em"
rounded:
  control: "6px"
  field: "5px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "40px"
components:
  button-primary:
    backgroundColor: "{colors.field-amber}"
    textColor: "#2c200a"
    rounded: "{rounded.control}"
    padding: "10px 15px"
    height: "44px"
  input:
    backgroundColor: "#ffffff"
    textColor: "#172533"
    rounded: "{rounded.field}"
    padding: "9px 10px"
    height: "42px"
  tag:
    backgroundColor: "#eef4f6"
    textColor: "#234657"
    rounded: "{rounded.pill}"
    padding: "3px 8px"
---

## Overview

**Creative North Star: "The Field Survey Ledger."** The interface treats each job as an observed record whose source facts, match evidence, and application decisions remain traceable. Pale paper surfaces sit on a cool canvas; navy survey ink establishes authority, while restrained amber marks the action that moves the search forward.

The visual system favors ruled bands, stamps, dense measurements, and explicit labels. It uses no remote fonts or decorative imagery, supporting the product's local and private character.

**Key Characteristics:**

- Pale technical paper on a cool gray-blue workspace.
- Navy identity fields and source stamps with amber action marks.
- Compact, tabular evidence arranged in ruled ledger rows.
- Square-edged containers with gently curved controls and pill-shaped evidence tags.
- Motion reserved for loading, dialogs, and direct interaction feedback.

## Colors

Survey ink is the primary structural color for the masthead, source stamps, headings, and strong controls. Paper and paper-deep form the working surfaces, while canvas separates the local workspace from its records. Field amber identifies the primary search action and visible keyboard focus. Link blue handles navigational actions. Danger and success always appear with text or an icon.

**The Amber Instrument Rule.** Reserve amber for primary action, focus, and small field marks so it remains immediately legible.

**The Ruled Evidence Rule.** Separate dense job facts with rule colors and tonal bands before reaching for shadows.

## Typography

The dashboard uses a local system sans-serif stack throughout; it never downloads a font. Display text is compact and heavy. Titles use strong weight and tight tracking. Body text stays plain and readable. Labels are small, weighted, and often uppercase with tracking to resemble field annotations.

**The Measured Label Rule.** Uppercase, tracked labels identify facts and sections; sentences and user-entered notes remain normal case.

## Layout

The desktop workspace has a maximum width of 1600px with 20px gutters. The first viewport reads in a fixed sequence: identity and actions, status tally, survey controls, saved views, then the lead register. Job rows use four evidence columns above 1250px, two structured bands at intermediate widths, and a single vertical record below 650px. The filter grid follows the same six-to-four-to-two-to-one collapse.

Spacing follows a 4px/8px family, with 16px record padding and 24px section spacing. Mobile controls expand to the available width, while job evidence remains in the same reading order.

**The Evidence Transect Rule.** Preserve identity, reasons, source facts, then manual tracking in that order at every breakpoint.

## Elevation & Depth

The system is flat by default. Rules and tonal changes organize records. Broad, low-contrast shadows lift the two main work surfaces; stronger shadows are limited to the detail drawer, dialogs, and the primary masthead action.

**The Working Surface Rule.** Use elevation for a whole task surface or modal layer, never for every job row.

## Shapes

Primary containers and ledger rows remain square. Inputs and buttons use gently curved corners. Match tags use a full pill. The square product mark and amber corner tab are the recurring signature geometry.

**The Instrument Shape Rule.** Keep work surfaces rectilinear and reserve rounding for controls and evidence tags.

## Components

### Buttons

- **Primary:** Amber with dark text, a 44px minimum target, compact padding, and a subtle shadow.
- **Quiet:** Transparent or paper-filled with a visible rule border.
- **Danger:** Red with white text, used only inside an explicit confirmation dialog.
- **Focus:** A 3px amber outline with a 2px offset. Press feedback moves the control by 1px without shifting surrounding layout.

### Chips

Match reasons use cool paper-blue pills with a rule border. Preferred-company tags use amber-tinted paper and stale tags use a warm gray-brown treatment. Every state is written in text.

### Cards / Containers

The filter panel and lead register are paper surfaces with broad ambient elevation. Individual jobs are ruled rows within the register rather than standalone cards.

### Inputs / Fields

Fields use a white surface, dark text, visible gray-blue border, persistent label, and 42px minimum height. Tracking selects strengthen the border with survey ink. Textareas resize vertically and notes require an explicit save action.

### Lead Register

Each record keeps job identity, deterministic reasons, source measurements, and manual tracking in adjacent evidence bands. Dashed rules distinguish stacked bands on tablet and mobile. Original links remain visually separate from dashboard write controls.

## Do's and Don'ts

### Do:

- **Do** keep source facts and match reasons visible near every job.
- **Do** use the system font stack and local vector icons so the interface remains self-contained.
- **Do** preserve the evidence-transect reading order across responsive layouts.
- **Do** pair status colors with labels, icons, or explanatory copy.
- **Do** keep interactive targets at least 44px where space allows and always show keyboard focus.

### Don't:

- **Don't** turn jobs into detached rounded cards; keep them within the ruled ledger.
- **Don't** use amber as general decoration or for secondary actions.
- **Don't** hide tracking writes behind autosave or ambiguous controls.
- **Don't** add remote fonts, stock imagery, ornamental gradients, or decorative animation.
