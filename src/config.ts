import "dotenv/config";
import { z } from "zod";

const schema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("production"),
  PORT: z.coerce.number().int().min(1).max(65535).default(8080),
  HOST: z.string().default("0.0.0.0"),
  REANIME_BASE_URL: z.string().url().default("https://reanime.to"),
  REQUEST_TIMEOUT_MS: z.coerce.number().int().min(1000).max(60000).default(15000),
  RATE_LIMIT_WINDOW_MS: z.coerce.number().int().min(1000).default(60000),
  RATE_LIMIT_MAX: z.coerce.number().int().min(1).default(90),
  CORS_ORIGINS: z.string().default("*")
});

const parsed = schema.safeParse(process.env);
if (!parsed.success) {
  console.error("Invalid environment configuration", parsed.error.flatten().fieldErrors);
  process.exit(1);
}
export const config = parsed.data;
export const corsOrigins = config.CORS_ORIGINS === "*"
  ? "*"
  : config.CORS_ORIGINS.split(",").map((origin) => origin.trim()).filter(Boolean);