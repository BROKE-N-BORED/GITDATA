"""Run the Dataset Scout window: python -m scout"""

from .updater import apply_pending_update, restart_app

if __name__ == "__main__":
    # A deferred update (v0.1.5 U6): "Later" at the restart prompt
    # leaves the verified zip staged, and it completes here, before
    # the window opens. The re-exec is required: applying without
    # it would leave the old code running. With nothing pending
    # this is one file-existence check and stays silent. A broken
    # pending update must never stop the app starting.
    try:
        if apply_pending_update():
            restart_app()
    except Exception:
        pass
    from .gui import main
    raise SystemExit(main())
