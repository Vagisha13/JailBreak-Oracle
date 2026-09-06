# Jailbreak Oracle — frontend image (Next.js 16 production build).
#
# NEXT_PUBLIC_API_URL is baked in at build time (browser-side env vars cannot be
# injected at runtime). Pass it as a --build-arg when building for a real
# deployment; the CI/verification build uses the localhost default.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
# Run as an unprivileged user.
RUN addgroup -S nodejs && adduser -S nextjs -G nodejs
COPY --chown=nextjs:nodejs --from=builder /app/package.json ./package.json
COPY --chown=nextjs:nodejs --from=builder /app/package-lock.json ./package-lock.json
COPY --chown=nextjs:nodejs --from=builder /app/node_modules ./node_modules
COPY --chown=nextjs:nodejs --from=builder /app/.next ./.next
COPY --chown=nextjs:nodejs --from=builder /app/public ./public
COPY --chown=nextjs:nodejs --from=builder /app/next.config.ts ./next.config.ts
USER nextjs
EXPOSE 3000
CMD ["npm", "start"]