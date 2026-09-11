# Security

Credentials are Pydantic SecretStr values, excluded from frontend payloads. Dedicated paper/live credentials never cross modes. Adapter errors are sanitized. The audit layer rejects fields containing secret, password, API-key or authorization names. Never send arbitrary configuration dictionaries to an API or log.

`.env`, runtime data, tokens and databases are gitignored and excluded from images. No real `.env` or credentials were generated. The optional operator token command writes a private 0600 file and does not print the token. Do not commit that file. Use a deployment secret manager for real credentials.

Without a configured operator token the app permits local read-only views and bounded research jobs; all broker controls are locked. With a token, all private API reads and writes require authentication. A login exchanges the token for a one-hour server-side session with an HttpOnly SameSite=Strict cookie. Every mutating browser request rejects a foreign Origin. TrustedHostMiddleware restricts hostnames; add your exact reverse-proxy hostname through ALLOWED_HOSTS. The CLI refuses a non-loopback bind without a control token. No CORS wildcard is enabled.

Use HTTPS at a trusted reverse proxy and secure its access; the local cookie is designed for loopback development. Do not expose the local development server directly to the public internet. Keep the reverse proxy's origin/host aligned and set SECURE_COOKIES=true before an internet-facing deployment. The service has no LLM, arbitrary-order endpoint, dynamic strategy-code upload or model-pickle import.

CI runs a local credential-pattern scan and gitleaks. They are defense in depth, not proof that no secret exists. Hash-chained audits are not immune to database-administrator rewrite. Back up encrypted persistent storage and anchor audit hashes outside the database if operating with real funds.
