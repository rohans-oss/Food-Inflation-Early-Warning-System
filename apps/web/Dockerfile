FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_URL=""
ARG NEXT_PUBLIC_MAP_PMTILES=""
ARG NEXT_PUBLIC_MAP_STYLE_URL=""
ARG NEXT_PUBLIC_MAP_TILES=""
ARG NEXT_PUBLIC_MAP_ATTRIBUTION=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_MAP_PMTILES=$NEXT_PUBLIC_MAP_PMTILES NEXT_PUBLIC_MAP_STYLE_URL=$NEXT_PUBLIC_MAP_STYLE_URL \
    NEXT_PUBLIC_MAP_TILES=$NEXT_PUBLIC_MAP_TILES NEXT_PUBLIC_MAP_ATTRIBUTION=$NEXT_PUBLIC_MAP_ATTRIBUTION
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
