import argparse
import sys

from smtp_proxy import service_manager


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        if not getattr(sys, "frozen", False):
            print("SCM hosting requires the installed frozen app_service.exe.", file=sys.stderr)
            return 1
        import servicemanager

        from smtp_proxy.windows_service import SMTPProxyService

        try:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(SMTPProxyService)
            servicemanager.StartServiceCtrlDispatcher()
            return 0
        except Exception as error:
            print(f"SCM dispatch failed ({type(error).__name__}).", file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description="SMTPProxy service administration")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("install", "start", "stop", "restart", "remove"):
        commands.add_parser(command)
    startup = commands.add_parser("set-startup")
    mode = startup.add_mutually_exclusive_group(required=True)
    mode.add_argument("--auto", action="store_true", dest="automatic")
    mode.add_argument("--manual", action="store_false", dest="automatic")
    options = parser.parse_args(arguments)
    try:
        if options.command == "set-startup":
            service_manager.set_autostart(options.automatic)
        else:
            getattr(service_manager, options.command)()
        return 0
    except Exception as error:
        code = getattr(error, "winerror", None)
        if isinstance(error, service_manager.ServiceManagerError):
            message = str(error)
        elif isinstance(error, PermissionError):
            message = "Administrator access or protected-file permissions are insufficient."
        elif isinstance(error, TimeoutError):
            message = "Service operation timed out; inspect SCM status and service.log."
        else:
            message = f"Service command failed ({type(error).__name__}, Windows code {code})."
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())