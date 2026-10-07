# Confirm before replacing the session; drag-and-drop to open

## Confirm

Open Files, Open Folder and dropped files replace the loaded batch. When one
is loaded, `_confirm_replace_session()` asks first ("Close current
session?"): **Open** or **Cancel** (Cancel is the default and Escape button).
The message says edits are kept, which is true: `save_catalog()` persists
them and reopening the same files restores them. No prompt on an empty
window (command-line start). The merge "Replace originals" flow has its own
confirmation and is unchanged.

## Drag and drop

Files or folders dragged from Finder/Explorer onto any part of the window
open through `load_paths()` (the existing command-line seam: same
validation, TIFF question and error report). The window accepts drops, and
its event filter sits on the window plus the few children that accept drops
themselves (canvas viewport, text boxes), never app-wide. It takes only EXTERNAL drags (`event.source()` is None) that carry local
file URLs in-app drags pass through. The
load is deferred with a zero-length timer so the confirm/TIFF dialogs don't
run inside the platform's drop callback.

Because the filter also sits on child text boxes, the window's pre-existing Up/Down
thumbnail-navigation handling is scoped to the window object itself, so arrow
keys still work in text and spin boxes.

Tests: `tests/test_open_confirm_and_drop.py`.
