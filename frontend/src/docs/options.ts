/** Swagger UI for the public agent schema only; nothing leaves this origin. */
export const agentDocsOptions = {
  url: '/api/agent/v1/openapi.json',
  // Never send the schema to swagger.io's online validator.
  validatorUrl: null,
  // Keep a pasted token in memory only, never in localStorage.
  persistAuthorization: false,
  deepLinking: false,
  displayRequestDuration: true,
  tryItOutEnabled: false,
} as const;
