# File → Close Session…

The user, 2026-11-08: "a close session option in the file menu … a line break
after capture; close session [prompt appears explaining that all files loaded
will close but files remain on disk - yes or no], line break, quit".

## Menu
New Roll… · Open Files · Open Folder · Hot Folder Tether… · Capture… ── Close
Session… ── Export… ── Settings… ── Quit (Ctrl+Q). Quit is kept in the File
menu on a Mac (`QAction.NoRole`; Qt would move it to the application menu)
and closes the window, so it goes through the usual Quit? question.

## Behaviour
- Nothing loaded: a hint "Nothing is loaded.", no question.
- Otherwise `_confirm_close_session(n)` (the test seam): "Close all N loaded
  images?" / "The files stay on disk, and your edits are saved and come back
  when you open them again." Yes / No, No the default.
- Yes = New Roll's body without its own question (`new_roll(confirmed=True,
  session=True)`): catalogue saved, images closed, the roll's black / white
  point and film stock reset, Needs attention emptied; Hot Folder Tether, if
  on, keeps watching. Hint: "Closed N images: edits saved, the files are
  still on disk." Nothing on disk is touched.

## Tests
`tests/test_close_session.py`: menu order and lines, Quit's role and shortcut,
No keeps everything, Yes saves and closes and deletes nothing, nothing loaded
doesn't ask, the box's text / buttons / default. Also fixed: the menu tests
lost the File menu when PySide dropped a temporary wrapper mid-loop (they now
keep the wrappers), which was the old `test_new_roll_is_first_in_the_file_menu`
and `test_file_menu_opens_one_monitor` failures.
