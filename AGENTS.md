# Local Agent Rules

- Do not read `.env` unless the user explicitly authorizes it for the current task.
- Never print, paraphrase, or summarize secret values from `.env`.
- Prefer debugging via runtime config objects, masked outputs, stack traces, and API responses instead of reading `.env`.
- If an environment variable must be checked, ask first and inspect only the named key that is necessary for the task.
