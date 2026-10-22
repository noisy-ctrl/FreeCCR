# Scrolls go to the widget under the pointer

## Problem (2026-10-22)

On the user's Mac (mouse), from the moment the app opened, the wheel did not
scroll the thumbnails or the right-hand panel: every notch zoomed the
picture, wherever the pointer was.

Qt 6 (6.7.1 here; the code is unchanged through 6.9) locks a scroll
*sequence* onto the widget it started on: a trackpad or smooth-scrolling
mouse sends ScrollBegin ... ScrollEnd, and in between
`QApplicationPrivate::wheel_widget` receives every event wherever the pointer
goes (`QApplication::notify`, `case QEvent::Wheel`). The lock is released
only by a ScrollEnd. If a sequence's ScrollEnd is lost (Qt drops wheel events
while a popup or modal box is up, before it reaches the release; some
drivers never send one), the lock stays. A plain mouse wheel has no phases,
so it never ends a sequence — and Qt still hands its events to the locked
widget (`Q_ASSERT(phase != Qt::NoScrollPhase)`, compiled out in release). On a
Mac with a trackpad and a mouse, a touch on the trackpad over the picture is
enough to leave every later mouse-wheel notch zooming it.

Not reproducible off a Mac: real X11 wheel events (XTest on Xvfb) scroll both
panes normally, before and after this change; X11 sends no phases.

## Fix (`src/ui/wheel_routing.py`)

`WheelRouter`, an application-wide event filter installed in `main.py`
(`install_wheel_router`), sees each wheel event twice: first the
spontaneous event to the QWindow (where the pointer is: `globalPosition`,
`phase`, `timestamp`), then Qt's first delivery to a widget (same
timestamp). If that widget is neither the widget under the pointer nor one
of its ancestors / descendants (ordinary routing), the router delivers the
event to the widget under the pointer itself — walking up the parents until
one accepts, as Qt's own propagation does, since a re-sent event isn't
propagated — and swallows the misrouted delivery. A phased sequence keeps
its own lock: after a ScrollBegin, the rest of the sequence is routed to
where it began (Qt's intent), and released at ScrollEnd. Later deliveries of
the same event (propagation, a scroll area forwarding to its scroll bar) and
correctly routed events are never touched (`redirected` counts re-routes).

Nothing else changes: a slider under the pointer still takes the wheel, the
picture still zooms when the pointer is over it.

## Tests (`tests/test_wheel_routing.py`)

A misrouted (latched) wheel scrolls the list under the pointer and doesn't
zoom; correct routing is left alone; only the first delivery of an event is
judged (propagation and scroll-bar forwarding untouched, other events
ignored); a trackpad gesture keeps its lock until ScrollEnd, then the picture
zooms normally; install is once per app; in the real MainWindow a wheel
delivered to the picture while the pointer is over the right-hand panel or
the thumbnails scrolls those and doesn't zoom. Checked by hand on Xvfb with
real X wheel events: both panes scroll, the picture zooms, a slider under
the pointer moves, zero re-routes.
