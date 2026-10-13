"""Absolute imports allow the same entry point to run inside a frozen bundle."""
import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from vmix_youtube_uploader.packaging_check import run
        raise SystemExit(run(sys.argv[2]))
    from vmix_youtube_uploader.main import main
    raise SystemExit(main())
