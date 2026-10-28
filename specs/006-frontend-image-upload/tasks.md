# Tasks: Frontend image upload

- [x] T1 Write failing tests in `tests/test_agent_invoke.py`: image forwarded to runtime; image-only uses default prompt; invalid image shapes/types/size return 400 without runtime call; 401 precedes validation; text-only unchanged; logs contain no image/prompt (AC4-9).
- [x] T2 Write failing tests in `tests/test_agent_frontend_page.py` for the file input, accepted types, preview/remove controls, and size/type limit constants (AC1-3).
- [x] T3 Implement image validation and forwarding in `src/agent_invoke/handler.py` (AC4-9).
- [x] T4 Implement attach/preview/remove/send UI in `src/agent_frontend/agent.html` (AC1-4).
- [x] T5 Check CSP/headers in `src/agent_frontend/handler.py` allow `data:` images; adjust with a test if needed (AC3).
- [x] T6 Run pytest (excluding the pre-existing IASA collection failures) and `terraform validate`; confirm no infra diff beyond spec 005.
- [ ] T7 After deploy approval, manually smoke-test upload in the browser (AC1-9).
