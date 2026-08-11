# QueekSync GUI Improvement Plan

## Current State Analysis

### Architecture
- **Framework**: customtkinter (Python tkinter wrapper)
- **Theme**: Dark glass aesthetic with blue accent (#3b82f6)
- **Pages**: Dashboard, Profiles, Monitor, Settings
- **Key Files**:
  - `src/ui/app.py` - Main window & navigation
  - `src/ui/dashboard.py` - Profile cards grid (3-column hardcoded)
  - `src/ui/sidebar.py` - Navigation sidebar
  - `src/ui/components.py` - Reusable UI components
  - `src/ui/theme.py` - Design tokens

### Issues Identified

#### 1. Mouse Scroll Not Working
**Root Cause**: The `CTkScrollableFrame` in the dashboard may not have proper scroll binding. CustomTkinter's scrollable frames sometimes need explicit scrollbar configuration.

**Fix Location**: `dashboard.py` lines 279-285

#### 2. Dashboard Grid Hardcoded to 3 Columns
**Root Cause**: Lines 299-305 hardcode `col_count = 3` and `card_width = 300` without considering window width.

**Fix Location**: `dashboard.py` lines 298-311

#### 3. Visual Design Improvements Needed
- Sidebar navigation uses emoji icons (user prefers QPainter-style geometric icons)
- Profile cards could have better visual hierarchy
- Stats row could be more prominent
- Overall polish and modern feel

---

## Implementation Plan

### Phase 1: Critical Fixes (High Priority)

#### 1.1 Fix Mouse Scrolling
**File**: `src/ui/dashboard.py`

**Changes**:
- Ensure `CTkScrollableFrame` has proper scrollbar configuration
- Add explicit scrollbar button styling
- Verify scroll event propagation

**Code Changes**:
```python
scroll = ctk.CTkScrollableFrame(
    self,
    fg_color="transparent",
    scrollbar_button_color=T.BORDER,
    scrollbar_button_hover_color=T.BORDER_BRIGHT,
    scrollbar_step=50,  # Add this for smoother scrolling
)
```

#### 1.2 Dynamic Grid Columns
**File**: `src/ui/dashboard.py`

**Changes**:
- Calculate column count based on window width
- Minimum card width: 280px
- Maximum: 4 columns (for wide screens)
- Auto-adjust on window resize

**Algorithm**:
```python
def _calculate_columns(self, container_width):
    min_card_width = 280
    gap = T.CARD_GAP
    max_cols = 4
    available_width = container_width - (gap * (max_cols + 1))
    cols = max(1, min(max_cols, available_width // (min_card_width + gap)))
    return cols
```

**Implementation**:
- Bind `<Configure>` event to main content frame
- Recalculate columns on resize
- Store container reference for width calculations

---

### Phase 2: Visual Polish (Medium Priority)

#### 2.1 Replace Emoji Icons with Geometric Symbols
**Files**: `src/ui/sidebar.py`, `src/ui/dashboard.py`

**Changes**:
- Replace emoji nav icons with Unicode geometric shapes or CTkFont symbols
- Use: ⬡ (hexagon), ☰ (menu), ◉ (target), ⚙ (gear) are acceptable as they render consistently

**Current**:
```python
NAV_ITEMS = [
    ("dashboard", "⬡  Dashboard", ...),
    ("profiles", "☰  Profiles", ...),
    ...
]
```

**Improved**: Keep Unicode symbols but ensure font fallback

#### 2.2 Enhanced Dashboard Stats
**File**: `src/ui/dashboard.py`

**Changes**:
- Add total files synced counter
- Add last sync time across all profiles
- Visual separator between stats and cards

#### 2.3 Profile Card Improvements
**File**: `src/ui/dashboard.py` - `ProfileCard` class

**Changes**:
- Add hover effect (subtle border highlight)
- Improve path display with truncation
- Add file count indicator
- Better button spacing

---

### Phase 3: Advanced Improvements (Nice to Have)

#### 3.1 Settings Panel Enhancements
**File**: `src/ui/settings_panel.py`

**Changes**:
- Add accent color picker (currently only theme dark/light)
- Add window size presets
- Add sync speed settings

#### 3.2 Profile Editor Polish
**File**: `src/ui/profile_editor.py`

**Changes**:
- Better tab navigation
- Add validation feedback
- Improved SFTP browser integration

---

## Technical Implementation Details

### Fix 1: Dynamic Grid (dashboard.py)

Replace the hardcoded grid calculation with dynamic calculation:

```python
def _build(self) -> None:
    profiles = self._app.profile_mgr.all()
    # ... existing code ...
    
    # Dynamic column calculation
    self._update_card_grid(profiles)
    
    # Bind resize event
    self._app.root.bind('<Configure>', self._on_window_resize)

def _update_card_grid(self, profiles):
    """Recalculate and rebuild card grid based on available width."""
    # Clear existing cards
    for child in self._scroll.winfo_children():
        if isinstance(child, ProfileCard):
            child.destroy()
    
    # Calculate columns
    container_width = self._scroll.winfo_width()
    if container_width < 100:  # Not visible yet
        col_count = 3
    else:
        min_card_width = 280
        gap = T.CARD_GAP
        max_cols = 4
        available = container_width - (gap * (max_cols + 1))
        col_count = max(1, min(max_cols, available // (min_card_width + gap)))
    
    # Configure grid
    for col_ in range(col_count):
        self._scroll.grid_columnconfigure(col_, weight=0, minsize=min_card_width + gap, pad=gap)
    self._scroll.grid_columnconfigure(col_count, weight=1)
    
    # Place cards
    for idx, profile in enumerate(sorted(profiles, key=lambda p: p.name)):
        row_ = idx // col_count
        col_ = idx % col_count
        card = ProfileCard(self._scroll, profile, self._app)
        card.grid(row=row_, column=col_, padx=(0, gap), pady=gap//2, sticky="nw")

def _on_window_resize(self, event):
    """Handle window resize to recalculate grid."""
    if hasattr(self, '_scroll') and self._scroll.winfo_exists():
        self.after(100, lambda: self._update_card_grid(self._app.profile_mgr.all()))
```

### Fix 2: Scroll Enhancement

In `dashboard.py`, ensure scrollable frame is properly configured:

```python
scroll = ctk.CTkScrollableFrame(
    self,
    fg_color="transparent",
    scrollbar_button_color=T.BORDER,
    scrollbar_button_hover_color=T.BORDER_BRIGHT,
    scrollbar_corner_radius=T.RADIUS_SM,
)
```

Also ensure the scrollable frame gets focus properly. Sometimes adding:
```python
scroll.bind('<MouseWheel>', self._on_mousewheel)
scroll.bind('<Button-4>', self._on_mousewheel)
scroll.bind('<Button-5>', self._on_mousewheel)
```

With handler:
```python
def _on_mousewheel(self, event):
    self._scroll.invoke("scroll", event.delta, "units")
```

---

## Design Token Updates (theme.py)

Consider adding:
```python
# New tokens for enhanced UI
GLOW_ACCENT = "#60a5fa"      # Lighter blue for hover states
CARD_HOVER  = "#1e293b"      # Slightly lighter on hover
SHADOW      = "#000000"      # For potential shadow effects
```

---

## Testing Checklist

- [ ] Mouse wheel scrolls dashboard cards
- [ ] Grid adjusts from 1-4 columns based on window width
- [ ] Resize handles smoothly without flicker
- [ ] Cards maintain proper spacing at all widths
- [ ] No console errors during scroll/resize
- [ ] Profiles panel still works correctly
- [ ] Monitor panel scroll still works

---

## Priority Order

1. **Critical**: Fix scroll functionality
2. **Critical**: Fix dynamic grid columns
3. **High**: Polish profile cards with hover effects
4. **Medium**: Enhance stats display
5. **Low**: Settings panel improvements

---

## Estimated Impact

- **Scroll Fix**: 15 lines of code, high user impact
- **Dynamic Grid**: 40 lines of code, high user impact
- **Visual Polish**: 30 lines, medium user impact

Total estimated change: ~85 lines across 2-3 files.
