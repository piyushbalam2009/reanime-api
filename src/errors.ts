export class AppError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code = "REQUEST_FAILED",
    public readonly upstreamStatus?: number
  ) {
    super(message);
    this.name = "AppError";
  }
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected error";
}