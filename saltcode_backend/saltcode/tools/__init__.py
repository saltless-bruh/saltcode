"""CLI entrypoints the Saltcode extension invokes via ``pi.exec`` (REQ-EXT-004).

Every backend capability gets one module here, runnable as
``python -m saltcode.tools.<name>``, printing JSON on stdout and returning a
documented exit code. The registered-tool table is design §5.3; the
consolidation gate is Task 7b.
"""
