import sys

def main():
    args = sys.argv[1:]
    if "--cli" in args:
        from ui.cli import run_cli
        run_cli()
    elif "--gui" in args:
        from ui.gui import launch_gui
        launch_gui()
    else:
        # Modern default: local web app (zero dependencies, works everywhere)
        from ui.web_ui import run_server
        run_server()

if __name__ == "__main__":
    main()
