FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# Build-time placeholder only: never used by the running application.
RUN DATABASE_URL=postgresql://build:build@127.0.0.1:5432/build npm run build

FROM node:22-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app ./
USER node
EXPOSE 3000
CMD ["sh", "-c", "npx drizzle-kit push && npm run start"]
