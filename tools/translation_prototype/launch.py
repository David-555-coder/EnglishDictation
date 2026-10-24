"""Separate prototype entry point; never imports the production app entry."""
if __name__ == '__main__':
    import os
    import sys
    if len(sys.argv)==3 and sys.argv[1]=='--verify-prototype':
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from tools.translation_prototype.verify import main
        raise SystemExit(main(sys.argv[2]))
    from tools.translation_prototype.ui import main
    raise SystemExit(main())
