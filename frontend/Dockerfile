# MM CostIQ web app: Next.js standalone server.
# COSTIQ_API_URL is read at runtime by the /api route handler, so one image works against any backend address.
FROM node:24-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 COSTIQ_STANDALONE=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npx next build

FROM node:24-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0 COSTIQ_API_URL=http://backend:8080
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD node -e "fetch('http://127.0.0.1:3000/sign-in').then((r) => process.exit(r.ok ? 0 : 1), () => process.exit(1))"
CMD ["node", "server.js"]
