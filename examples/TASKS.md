# TASKS

### TASK-001: Add user authentication endpoint
- **Статус**: open
- **Приоритет**: P1
- **Зависимости**: —
- **Файлы (новые)**: src/auth/handler.py, src/auth/tokens.py
- **Файлы (modify)**: src/routes.py
- **Описание**: Implement JWT-based authentication with login and refresh endpoints. Use bcrypt for password hashing. Return access and refresh tokens on successful login.
- **Критерий готовности**: POST /auth/login returns 200 with tokens for valid credentials, 401 for invalid. POST /auth/refresh returns new access token for valid refresh token.
- **Дискуссия**: —
- **Агент**: —
- **Ветка**: —

### TASK-002: Add rate limiting middleware
- **Статус**: open
- **Приоритет**: P2
- **Зависимости**: —
- **Файлы (новые)**: src/middleware/rate_limit.py
- **Файлы (modify)**: src/app.py
- **Описание**: Add per-IP rate limiting middleware. Default limit 100 requests per minute. Return 429 Too Many Requests when exceeded.
- **Критерий готовности**: Requests beyond the limit receive 429 status. Rate limit headers present in all responses.
- **Дискуссия**: —
- **Агент**: —
- **Ветка**: —

### TASK-003: Refactor database connection pool
- **Статус**: open
- **Приоритет**: P2
- **Зависимости**: TASK-001
- **Файлы (новые)**: —
- **Файлы (modify)**: src/db/pool.py, src/db/config.py
- **Описание**: Replace single-connection setup with a connection pool. Pool size configurable via DB_POOL_SIZE env var. Add GET /health endpoint.
- **Критерий готовности**: Application uses connection pool. GET /health returns DB status. Pool size configurable.
- **Дискуссия**: —
- **Агент**: —
- **Ветка**: —
