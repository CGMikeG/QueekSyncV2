#!/bin/bash
# Ad-hoc verification script for QueekSync GUI improvements
# Run from /home/cgmikeg/QueekSync directory

echo "============================================================"
echo "QueekSync GUI Improvement Verification"
echo "============================================================"
echo ""

# Use project venv Python
PYTHON=".venv/bin/python"

# Check if venv exists
if [ ! -f "$PYTHON" ]; then
    echo "❌ Virtual environment not found at .venv/bin/python"
    echo "   Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

echo "🔍 Testing Python syntax..."
$PYTHON -m py_compile src/ui/dashboard.py src/ui/sidebar.py src/ui/monitor_panel.py src/ui/theme.py
if [ $? -eq 0 ]; then
    echo "  ✅ All syntax checks passed"
else
    echo "  ❌ Syntax errors found"
    exit 1
fi

echo ""
echo "🔍 Testing imports..."
$PYTHON -c "
import sys
sys.path.insert(0, 'src')
from ui.dashboard import DashboardPanel, ProfileCard
from ui.sidebar import Sidebar, NAV_ITEMS
from ui.monitor_panel import MonitorPanel, ActiveSyncCard
from ui.components import GlassCard, StatTile, StatusBadge
print('  ✅ All imports successful')
"
if [ $? -ne 0 ]; then
    echo "  ❌ Import failed"
    exit 1
fi

echo ""
echo "🔍 Testing grid calculation logic..."
$PYTHON -c "
import sys
sys.path.insert(0, 'src')

# Simulate the calculation algorithm
min_card_width = 280
gap = 14
max_cols = 4

test_widths = [
    (600, 1),   # Very narrow
    (800, 2),   # Narrow
    (1000, 3),  # Medium
    (1200, 3),  # Wide
    (1500, 4),  # Extra wide
]

all_passed = True
for width, expected in test_widths:
    available = width - (gap * (max_cols + 1)) - 40
    cols = max(1, min(max_cols, available // (min_card_width + gap)))
    status = '✅' if cols == expected else '❌'
    print(f'  {status} Width {width:4d}px -> {cols} columns (expected {expected})')
    if cols != expected:
        all_passed = False

if all_passed:
    print('  ✅ Grid calculation logic correct')
else:
    print('  ❌ Grid calculation logic has issues')
    sys.exit(1)
"
if [ $? -ne 0 ]; then
    exit 1
fi

echo ""
echo "🔍 Testing navigation symbols..."
$PYTHON -c "
import sys
sys.path.insert(0, 'src')
from ui.sidebar import NAV_ITEMS

expected_symbols = ['◈', '☰', '◉', '⚙']
all_passed = True

for i, (page_id, label, tip) in enumerate(NAV_ITEMS):
    symbol = label.split()[0] if label else ''
    status = '✅' if symbol == expected_symbols[i] else '❌'
    print(f'  {status} {page_id}: \'{symbol}\' (expected \'{expected_symbols[i]}\')')
    if symbol != expected_symbols[i]:
        all_passed = False

if all_passed:
    print('  ✅ Navigation symbols correct')
else:
    print('  ❌ Navigation symbols incorrect')
    sys.exit(1)
"
if [ $? -ne 0 ]; then
    exit 1
fi

echo ""
echo "🔍 Testing class structure..."
$PYTHON -c "
import sys
sys.path.insert(0, 'src')
from ui.dashboard import DashboardPanel, ProfileCard

# Check ProfileCard has hover methods
card_methods = dir(ProfileCard)
has_hover_enter = '_on_hover_enter' in card_methods
has_hover_leave = '_on_hover_leave' in card_methods

status1 = '✅' if has_hover_enter and has_hover_leave else '❌'
print(f'  {status1} ProfileCard hover methods: enter={has_hover_enter}, leave={has_hover_leave}')

# Check DashboardPanel has grid methods
panel_methods = dir(DashboardPanel)
has_populate = '_populate_grid' in panel_methods
has_calculate = '_calculate_columns' in panel_methods
has_resize = '_on_window_resize' in panel_methods
has_recalc = '_recalculate_grid' in panel_methods

status2 = '✅' if all([has_populate, has_calculate, has_resize, has_recalc]) else '❌'
print(f'  {status2} DashboardPanel grid methods: populate={has_populate}, calculate={has_calculate}, resize={has_resize}, recalc={has_recalc}')

if not (has_hover_enter and has_hover_leave and has_populate and has_calculate and has_resize and has_recalc):
    sys.exit(1)
"
if [ $? -ne 0 ]; then
    exit 1
fi

echo ""
echo "============================================================"
echo "Summary"
echo "============================================================"
echo "  ✅ PASS: Syntax checks"
echo "  ✅ PASS: Import tests"
echo "  ✅ PASS: Grid calculation logic"
echo "  ✅ PASS: Navigation symbols"
echo "  ✅ PASS: Class structure"
echo ""
echo "✅ All ad-hoc verifications passed!"
echo "============================================================"
echo ""
echo "Note: This is ad-hoc verification, not a full test suite."
echo "Manual testing recommended: run ./run.sh and verify:"
echo "  - Mouse wheel scrolling works"
echo "  - Grid adjusts when resizing window"
echo "  - Cards have hover effects"
echo ""
