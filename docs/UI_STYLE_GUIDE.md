# BNuvola AI Solutions - UI Style Guide

**Design System Documentation for JobPulse™ and Future Applications**

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [Color System](#color-system)
3. [Typography](#typography)
4. [Layout Structure](#layout-structure)
5. [Component Library](#component-library)
6. [Form Elements](#form-elements)
7. [Animations & Transitions](#animations--transitions)
8. [Responsive Design](#responsive-design)
9. [Customizing for Client Branding](#customizing-for-client-branding)

---

## Design Philosophy

Our design language emphasizes:

- **Premium & Modern**: Glass-morphism effects with subtle gradients
- **Dark-First**: Optimized for dark mode with high readability
- **Subtle Animation**: Micro-interactions that feel responsive without being distracting
- **Consistency**: Unified visual language across all components
- **Accessibility**: Sufficient contrast ratios and interactive feedback

---

## Color System

### Primary Palette

| Name | Hex | RGB | Usage |
|------|-----|-----|-------|
| **Primary Blue** | `#3b82f6` | `rgb(59, 130, 246)` | Primary actions, active states, links |
| **Primary Cyan** | `#06b6d4` | `rgb(6, 182, 212)` | Accent gradients, secondary highlights |
| **Gradient Primary** | `linear-gradient(135deg, #3b82f6, #06b6d4)` | - | Logo, icons, decorative elements |

### Background Colors

| Name | Value | Usage |
|------|-------|-------|
| **Sidebar BG** | `#0d1421` | Fixed sidebar background |
| **Content BG** | `linear-gradient(135deg, #1a2234 0%, #1e293b 50%, #0f172a 100%)` | Main content area |
| **Card BG** | `linear-gradient(145deg, rgba(30, 41, 59, 0.9), rgba(15, 23, 42, 0.95))` | Glass cards |
| **Card Hover BG** | `linear-gradient(145deg, rgba(35, 48, 70, 0.95), rgba(20, 30, 50, 0.98))` | Card hover states |

### Text Colors

| Name | Hex | Usage |
|------|-----|-------|
| **Primary Text** | `#f1f5f9` | Headings, important text |
| **Secondary Text** | `#e2e8f0` | Body text, labels |
| **Muted Text** | `#94a3b8` | Secondary info, navigation |
| **Subtle Text** | `#64748b` | Placeholders, hints, subtitles |

### Status Colors

| Status | Hex | RGBA Background |
|--------|-----|-----------------|
| **Blue (Info)** | `#3b82f6` | `rgba(59, 130, 246, 0.15)` |
| **Green (Success)** | `#678E6E` | `rgba(103, 142, 110, 0.15)` |
| **Cyan (Active)** | `#06b6d4` | `rgba(6, 182, 212, 0.15)` |
| **Orange (Warning)** | `#f97316` | `rgba(249, 115, 22, 0.15)` |
| **Red (Danger)** | `#ef4444` | `rgba(239, 68, 68, 0.15)` |
| **Purple (Accent)** | `#a855f7` | `rgba(168, 85, 247, 0.15)` |

### Border Colors

```css
--sidebar-border: rgba(255, 255, 255, 0.08);
--card-border: rgba(59, 130, 246, 0.15);
--card-border-hover: rgba(59, 130, 246, 0.3);
--input-border: rgba(59, 130, 246, 0.2);
```

---

## Typography

### Font Stack

```css
font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
```

**Accent Font (Handwritten):**
```css
font-family: 'Kalam', cursive; /* For "powered by" attributions */
```

### Font Weights

| Weight | Value | Usage |
|--------|-------|-------|
| Regular | `400` | Body text |
| Medium | `500` | Navigation, labels |
| Semi-Bold | `600` | Section titles, buttons |
| Bold | `700` | Page titles, stats |

### Font Sizes

| Element | Size | Line Height |
|---------|------|-------------|
| Page Title | `1.5rem` | `1.2` |
| Section Title | `1.125rem` | `1.3` |
| Body Text | `0.875rem` | `1.5` |
| Small Text | `0.75rem` | `1.4` |
| Nav Section Title | `0.7rem` | `1.3` |
| Stat Value | `2rem` | `1` |

### Text Styles

```css
/* Section titles (uppercase) */
.nav-section-title {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #64748b;
}

/* Gradient text for branding */
.brand-text {
    background: linear-gradient(135deg, #3b82f6, #06b6d4);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
```

---

## Layout Structure

### CSS Variables

```css
:root {
    /* Sidebar */
    --sidebar-width: 260px;
    --sidebar-collapsed-width: 70px;
    --sidebar-bg: #0d1421;
    --sidebar-border: rgba(255, 255, 255, 0.08);
    --sidebar-hover: rgba(255, 255, 255, 0.05);
    --sidebar-active: rgba(59, 130, 246, 0.15);
    --sidebar-active-border: #3b82f6;
    --sidebar-margin: 10px;  /* Floating margin */
    --sidebar-radius: 20px;  /* Rounded corners */
    
    /* Content */
    --content-bg: linear-gradient(135deg, #1a2234 0%, #1e293b 50%, #0f172a 100%);
    --header-height: 70px;
    
    /* Spacing */
    --content-padding: 2rem;
    --card-padding: 1.5rem;
    --border-radius-sm: 8px;
    --border-radius-md: 12px;
    --border-radius-lg: 16px;
    --border-radius-xl: 20px;
}
```

### Sidebar Styling (Floating Design)

The sidebar uses a "floating" design with rounded corners and shadow:

```css
.sidebar {
    position: fixed;
    left: 10px;      /* Floating margin */
    top: 10px;
    bottom: 10px;
    width: var(--sidebar-width);
    background: var(--sidebar-bg);
    border: 1px solid var(--sidebar-border);
    border-radius: 20px;  /* Rounded corners */
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(59, 130, 246, 0.1);
    display: flex;
    flex-direction: column;
    z-index: 1000;
}

/* Main content adjusts for floating sidebar */
.main-content {
    margin-left: calc(var(--sidebar-width) + 20px);  /* sidebar + margins */
}
```

### Sidebar Header (Centered Logo)

The sidebar header centers the logo and subtitle:

```css
.sidebar-header {
    padding: 1.5rem;
    border-bottom: 1px solid var(--sidebar-border);
    text-align: center;  /* Center logo and subtitle */
}

.sidebar-logo {
    display: inline-flex;  /* Changed from flex for centering */
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    text-decoration: none;
    color: #fff;
}
```

### Sidebar Navigation (Hidden Scrollbar)

The sidebar nav hides scrollbars while maintaining scroll functionality:

```css
.sidebar-nav {
    flex: 1;
    padding: 1rem 0;
    overflow-y: auto;
    
    /* Hide scrollbar - cross-browser */
    scrollbar-width: none;           /* Firefox */
    -ms-overflow-style: none;        /* IE/Edge */
}

.sidebar-nav::-webkit-scrollbar {
    display: none;  /* Chrome/Safari/Opera */
}
```

> [!NOTE]
> The scrollbar is hidden for a cleaner UI, but users can still scroll using mouse wheel, touch, or keyboard navigation.

### Page Layout

```
┌─────────────────────────────────────────────────────────────┐
│ ┌──────────┐ ┌────────────────────────────────────────────┐ │
│ │          │ │  Page Header (gradient bg)                 │ │
│ │          │ │  Title | Subtitle         | Actions        │ │
│ │  Sidebar │ ├────────────────────────────────────────────┤ │
│ │  (fixed) │ │                                            │ │
│ │          │ │  Content Area (padding: 2rem)              │ │
│ │  260px   │ │                                            │ │
│ │          │ │  ┌─────────┐ ┌─────────┐ ┌─────────┐       │ │
│ │          │ │  │  Card   │ │  Card   │ │  Card   │       │ │
│ │          │ │  └─────────┘ └─────────┘ └─────────┘       │ │
│ │          │ │                                            │ │
│ └──────────┘ └────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Sidebar Structure

```html
<aside class="sidebar">
    <div class="sidebar-header">
        <a class="sidebar-logo">
            <!-- NEW: Pulse heartbeat logo integrated into text -->
            <span class="sidebar-logo-text">
                <span class="logo-j">J</span>
                <span class="logo-pulse">
                    <svg viewBox="0 0 24 16" class="pulse-svg">
                        <path d="M0 8 L6 8 L8 3 L10 13 L12 6 L14 10 L16 8 L24 8" 
                              stroke="currentColor" stroke-width="2" fill="none"/>
                    </svg>
                </span>
                <span class="logo-rest">bPulse</span>
            </span>
        </a>
        <div class="sidebar-logo-sub">powered by BNuvola AI</div>
    </div>
    
    <nav class="sidebar-nav">
        <div class="nav-section">
            <div class="nav-section-title">Section Name</div>
            <a class="nav-item active">
                <i class="fas fa-icon"></i>
                <span>Item Label</span>
            </a>
        </div>
    </nav>
    
    <div class="sidebar-footer">
        <div class="user-info">
            <div class="user-avatar">A</div>
            <div class="user-details">
                <div class="user-name">Username</div>
                <div class="user-role">Role</div>
            </div>
        </div>
        <a class="logout-btn">
            <i class="fas fa-sign-out-alt"></i>
            <span>Logout</span>
        </a>
    </div>
</aside>
```

### Logo CSS (Pulse Heartbeat Design)

```css
.sidebar-logo-text {
    font-size: 1.5rem;
    font-weight: 700;
    display: flex;
    align-items: center;
}

.logo-j, .logo-rest {
    background: linear-gradient(135deg, #3b82f6, #06b6d4);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}

.logo-pulse {
    display: inline-flex;
    align-items: center;
    width: 24px;
    height: 16px;
    margin: 0 -2px;
}

.pulse-svg {
    width: 100%;
    height: 100%;
    color: #3b82f6;
}
```

---

## Component Library

### Glass Cards

The signature component of our design system:

```css
.glass-card {
    background: linear-gradient(145deg, rgba(30, 41, 59, 0.9), rgba(15, 23, 42, 0.95));
    border: 1px solid rgba(59, 130, 246, 0.15);
    backdrop-filter: blur(20px);
    border-radius: 16px;
    transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
    overflow: hidden;
    position: relative;
}

/* Top border accent on hover */
.glass-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: linear-gradient(90deg, #3b82f6, #06b6d4);
    opacity: 0;
    transition: opacity 0.3s ease;
}

.glass-card:hover {
    background: linear-gradient(145deg, rgba(35, 48, 70, 0.95), rgba(20, 30, 50, 0.98));
    border-color: rgba(59, 130, 246, 0.3);
    transform: translateY(-4px);
    box-shadow: 0 15px 40px rgba(0, 0, 0, 0.25), 0 0 30px rgba(59, 130, 246, 0.1);
}

.glass-card:hover::before {
    opacity: 1;
}
```

### Stat Cards

```html
<div class="glass-card stat-card">
    <div class="stat-icon blue">
        <i class="fas fa-briefcase"></i>
    </div>
    <div class="stat-value">42</div>
    <div class="stat-label">Active Items</div>
    <div class="stat-change positive">
        <i class="fas fa-arrow-up"></i> +12%
    </div>
</div>
```

Icon color variations: `blue`, `green`, `purple`, `orange`, `cyan`, `red`

### Action Cards (Quick Actions)

```html
<a href="/path" class="glass-card action-card">
    <div class="action-icon">
        <i class="fas fa-icon"></i>
    </div>
    <div class="action-title">Action Name</div>
    <div class="action-desc">Short description</div>
</a>
```

### Alerts

```css
.alert {
    background: linear-gradient(145deg, rgba(30, 41, 59, 0.9), rgba(15, 23, 42, 0.95));
    border: 1px solid rgba(59, 130, 246, 0.2);
    border-radius: 12px;
}

/* Variants */
.alert-info    { background: linear-gradient(145deg, rgba(6, 182, 212, 0.15), rgba(59, 130, 246, 0.1)); border-color: rgba(6, 182, 212, 0.3); }
.alert-success { background: linear-gradient(145deg, rgba(103, 142, 110, 0.15), rgba(16, 185, 129, 0.1)); border-color: rgba(103, 142, 110, 0.3); }
.alert-warning { background: linear-gradient(145deg, rgba(249, 115, 22, 0.15), rgba(234, 179, 8, 0.1)); border-color: rgba(249, 115, 22, 0.3); }
.alert-danger  { background: linear-gradient(145deg, rgba(239, 68, 68, 0.15), rgba(220, 38, 38, 0.1)); border-color: rgba(239, 68, 68, 0.3); }
```

### Tables

```css
.data-table {
    background: linear-gradient(145deg, rgba(30, 41, 59, 0.6), rgba(15, 23, 42, 0.7));
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid rgba(59, 130, 246, 0.1);
}

.data-table th {
    background: linear-gradient(135deg, rgba(59, 130, 246, 0.15), rgba(6, 182, 212, 0.08));
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #94a3b8;
    padding: 1rem;
    border-bottom: 1px solid rgba(59, 130, 246, 0.2);
}

.data-table td {
    padding: 1rem;
    border-bottom: 1px solid rgba(59, 130, 246, 0.08);
    color: #e2e8f0;
}

.data-table tbody tr:hover {
    background: rgba(59, 130, 246, 0.08);
}
```

---

## Form Elements

### Input Fields

```css
.form-control,
.form-select {
    background: linear-gradient(145deg, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.9));
    border: 1px solid rgba(59, 130, 246, 0.2);
    border-radius: 8px;
    color: #e2e8f0;
    padding: 0.75rem 1rem;
    transition: all 0.3s ease;
}

.form-control:focus,
.form-select:focus {
    background: linear-gradient(145deg, rgba(35, 48, 70, 0.9), rgba(20, 30, 50, 0.95));
    border-color: #3b82f6;
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15), 0 0 20px rgba(59, 130, 246, 0.1);
    outline: none;
}

.form-control::placeholder {
    color: #64748b;
}
```

### Checkboxes & Switches

```css
.form-check-input {
    background-color: rgba(30, 41, 59, 0.8);
    border: 1px solid rgba(59, 130, 246, 0.3);
}

.form-check-input:checked {
    background-color: #3b82f6;
    border-color: #3b82f6;
}

.form-check-input:focus {
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
}
```

### Buttons (Bootstrap Extended)

```css
/* Primary Button Enhancement */
.btn-primary {
    background: linear-gradient(135deg, #3b82f6, #2563eb);
    border: none;
    box-shadow: 0 4px 15px rgba(59, 130, 246, 0.3);
}

.btn-primary:hover {
    background: linear-gradient(135deg, #60a5fa, #3b82f6);
    box-shadow: 0 6px 20px rgba(59, 130, 246, 0.4);
    transform: translateY(-1px);
}

/* Outline Button Enhancement */
.btn-outline-primary {
    border-color: rgba(59, 130, 246, 0.5);
    color: #3b82f6;
}

.btn-outline-primary:hover {
    background: rgba(59, 130, 246, 0.15);
    border-color: #3b82f6;
}
```

---

## Animations & Transitions

### Timing Functions

```css
/* Standard ease */
transition: all 0.3s ease;

/* Bounce effect for cards */
transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);

/* Smooth slide */
transition: transform 0.3s ease, opacity 0.3s ease;
```

### Hover Effects

```css
/* Card lift */
.card:hover {
    transform: translateY(-4px);
    box-shadow: 0 15px 40px rgba(0, 0, 0, 0.25);
}

/* Subtle scale */
.icon:hover {
    transform: scale(1.1);
}

/* Glow effect */
.element:hover {
    box-shadow: 0 0 30px rgba(59, 130, 246, 0.2);
}
```

### Loading States

```css
/* Spinner */
@keyframes spin {
    to { transform: rotate(360deg); }
}

.spinner {
    animation: spin 1s linear infinite;
}

/* Pulse */
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

.loading {
    animation: pulse 2s ease-in-out infinite;
}
```

---

## Responsive Design

### Breakpoints

| Name | Value | Description |
|------|-------|-------------|
| `xs` | `< 576px` | Mobile phones |
| `sm` | `≥ 576px` | Small phones |
| `md` | `≥ 768px` | Tablets |
| `lg` | `≥ 992px` | Laptops |
| `xl` | `≥ 1200px` | Desktops |

### Key Responsive Rules

```css
/* Tablet: narrower sidebar */
@media (min-width: 992px) and (max-width: 1199px) {
    :root {
        --sidebar-width: 220px;
    }
}

/* Mobile: hidden sidebar with toggle */
@media (max-width: 991px) {
    .sidebar {
        transform: translateX(-100%);
    }
    
    .sidebar.open {
        transform: translateX(0);
    }
    
    .main-content {
        margin-left: 0;
    }
    
    .mobile-toggle {
        display: block;
    }
}

/* Small mobile: reduced padding */
@media (max-width: 576px) {
    .content-area {
        padding: 1rem;
    }
    
    .stat-value {
        font-size: 1.5rem;
    }
}
```

---

## Customizing for Client Branding

### Variables to Override

Create a `client-theme.css` file and override these key variables:

```css
:root {
    /* Primary brand color - change these for different clients */
    --brand-primary: #3b82f6;        /* Main brand color */
    --brand-secondary: #06b6d4;      /* Accent color for gradients */
    --brand-gradient: linear-gradient(135deg, var(--brand-primary), var(--brand-secondary));
    
    /* Sidebar can be customized */
    --sidebar-bg: #0d1421;
    --sidebar-active: rgba(59, 130, 246, 0.15);
    --sidebar-active-border: var(--brand-primary);
    
    /* Content area */
    --content-bg: linear-gradient(135deg, #1a2234 0%, #1e293b 50%, #0f172a 100%);
}
```

### Example: Different Color Schemes

**Purple/Pink Theme:**
```css
:root {
    --brand-primary: #a855f7;
    --brand-secondary: #ec4899;
}
```

**Green/Teal Theme:**
```css
:root {
    --brand-primary: #678E6E;
    --brand-secondary: #14b8a6;
}
```

**Orange/Yellow Theme:**
```css
:root {
    --brand-primary: #f97316;
    --brand-secondary: #eab308;
}
```

### Logo Replacement

Replace the logo icon and text in the sidebar header:

```html
<div class="sidebar-logo-icon">
    <img src="/path/to/client-logo.png" alt="Logo">
    <!-- OR use Font Awesome icon -->
    <i class="fas fa-custom-icon"></i>
</div>
<span class="sidebar-logo-text">ClientApp™</span>
<div class="sidebar-logo-sub">powered by BNuvola AI</div>
```

---

## Required External Resources

```html
<!-- Bootstrap Dark Theme -->
<link href="https://cdn.replit.com/agent/bootstrap-agent-dark-theme.min.css" rel="stylesheet">

<!-- Font Awesome Icons -->
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">

<!-- Google Fonts -->
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Kalam:wght@400;700&display=swap" rel="stylesheet">

<!-- Bootstrap JS (for modals, dropdowns, etc.) -->
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
```

---

## Quick Start Template

Copy `templates/base_layout.html` as your starting point. It includes:

- ✅ Complete sidebar with navigation
- ✅ Collapsible sidebar functionality
- ✅ Mobile-responsive design
- ✅ Page header with actions
- ✅ Flash message styling
- ✅ Glass card components
- ✅ User info and logout

---

*Last Updated: February 4, 2026*  
*Version: 1.2*  
*© BNuvola AI Solutions*
