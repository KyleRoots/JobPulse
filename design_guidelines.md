# JobPulse Design Guidelines

## Design Approach
**System-Based Approach**: Drawing from Carbon Design System (IBM) and Fluent Design principles for enterprise data-heavy applications. Bootstrap 5 dark theme as foundation with professional dashboard patterns from Linear, Asana, and Bullhorn's own interface conventions.

## Core Design Principles
- Information density with clarity
- Immediate actionability (all actions visible, minimal clicks)
- Status-first design (system health always visible)
- Consistent enterprise patterns

---

## Typography System

**Font Stack**: 
- Primary: 'Inter', system-ui, sans-serif (via Google Fonts CDN)
- Monospace: 'JetBrains Mono' for IDs, timestamps, data values

**Hierarchy**:
- Page Titles: 28px, font-weight 600
- Section Headers: 20px, font-weight 600
- Card Titles: 16px, font-weight 500
- Body Text: 14px, font-weight 400
- Table Data: 13px, font-weight 400
- Labels/Meta: 12px, font-weight 500, uppercase tracking-wide

---

## Layout System

**Spacing Primitives**: Tailwind units of 2, 4, 6, 8 for consistency
- Component padding: p-4, p-6
- Section gaps: gap-6, gap-8
- Card spacing: m-4, mb-6

**Grid Structure**:
- Sidebar: Fixed 240px width on desktop, collapsible on mobile
- Main Content: Fluid with max-width 1600px, px-6 py-4
- Dashboard Cards: 3-column grid (lg), 2-column (md), 1-column (mobile)

---

## Component Library

### Navigation
**Sidebar (Left)**:
- Fixed dark background (#1a1d23)
- Logo at top (h-16, p-4)
- Nav items with icons (Heroicons via CDN) + labels
- Active state: subtle left border (3px) + background highlight
- Grouped sections: Dashboard, Candidates, Jobs, ATS Sync, Settings
- User profile at bottom with status indicator

**Top Bar**:
- Height: h-14
- Search bar (w-80, placeholder: "Search candidates, jobs...")
- Right section: Notification bell (with badge count), sync status indicator, user avatar dropdown

### Status Indicators
**Sync Status Badge**: Pill shape, 8px dot + text
- Active/Syncing: animated pulse dot
- Success: static green dot
- Error: red dot with alert icon

**Candidate Pipeline Stages**: Horizontal stepper/progress bar
- Stages: Sourced → AI Screened → Interview → Submitted → Placed
- Current stage highlighted, completed stages show checkmark

### Data Tables
**Structure**:
- Sticky header row (background darker than table body)
- Alternating row backgrounds (subtle zebra striping)
- Row height: 56px minimum for touch targets
- Hover state: background lightens slightly
- Selected rows: border-left 3px accent + background highlight

**Column Design**:
- Checkbox column: 48px fixed
- Action column (right): 120px, visible action buttons (no overflow menu)
- Status columns: 100px, centered badges
- Text columns: flex with min-width constraints
- Sortable headers: arrow icon on hover

**Action Buttons in Tables**: 
- Icon buttons: 32px × 32px, tooltips on hover
- Primary action: "View Details" (eye icon)
- Secondary: "Quick Actions" dropdown (3 dots)
- Bulk actions bar: appears above table when rows selected

### Cards & Panels
**Dashboard Cards**:
- Border: 1px subtle
- Padding: p-6
- Header: flex justify-between with title + action icon
- Content area with appropriate data visualization
- Footer for metadata/timestamps

**Metric Cards** (KPIs):
- Large number display: 32px font-weight 700
- Label below: 12px uppercase
- Trend indicator: arrow + percentage (green/red)
- Sparkline chart: 80px height, subtle line graph

### Forms & Inputs
**Input Fields**:
- Height: 40px
- Border: 1px, increased border-width on focus
- Label above: 12px font-weight 500, mb-2
- Helper text below: 11px, muted
- Error state: red border + error message

**Buttons**:
- Primary: Height 40px, px-6, font-weight 500
- Secondary: Outlined variant
- Icon buttons: 36px square
- Button groups: segmented with shared borders

### Data Visualization
**Job Feed Activity**:
- Timeline view: vertical line with activity nodes
- Each node: timestamp + event type + candidate count
- Expandable for details

**AI Vetting Dashboard**:
- Score cards with radial progress indicators
- Compatibility percentage: large number + ring chart
- Skills match: tag cloud with confidence weights
- Resume parsing results: structured data cards

---

## Page-Specific Layouts

**Dashboard Home**:
- Top: 3 metric cards (Active Jobs, Candidates in Pipeline, Today's Interviews)
- Middle: 2-column (Recent AI Vetted Candidates table + Bullhorn Sync Status panel)
- Bottom: Job Feed Activity timeline

**Candidate Details**:
- Split view: 2/3 main content (profile, resume, AI analysis) + 1/3 sidebar (quick actions, status, notes)
- Tabbed interface: Overview, Resume, AI Scores, Activity History, Communications

**Jobs Board**:
- Filterable table with: Job Title, Client, Status, Candidates (count badge), Posted Date, Actions
- Bulk actions: Sync to Bullhorn, Change Status, Archive

---

## Icons
Use Heroicons (outline style) via CDN for all interface icons. Key icons needed:
- Navigation: home, users, briefcase, refresh, cog
- Actions: eye, pencil, trash, check, x-mark
- Status: check-circle, exclamation-triangle, clock, signal

---

## Images
**No hero images required** - this is a working dashboard application. 

**Profile/Avatar Images**: 
- Candidate thumbnails: 48px circle in tables, 120px on detail pages
- Client logos: 32px square in job listings
- User avatars: 32px circle in navigation

---

## Accessibility & Performance
- WCAG AA contrast ratios maintained throughout dark theme
- Keyboard navigation: visible focus rings (2px offset)
- Screen reader labels on all icon-only buttons
- Table data: proper semantic HTML with scope attributes
- Loading states: skeleton screens for data tables (not spinners)

---

**Critical Dark Theme Specifications**: Background hierarchy creates depth without relying on shadows - use 3-4 background shades (#0f1115 darkest → #1a1d23 cards → #24272e panels). Text contrast must meet WCAG standards with off-white (#e8eaed) for primary text, mid-gray for secondary.