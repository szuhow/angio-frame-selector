# --- Build stage ---
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# At build time the API is proxied via nginx, so we don't need vite proxy
RUN npm run build

# --- Production stage ---
FROM nginx:alpine

# Default backend port; can be overridden at runtime via -e BACKEND_PORT=...
ENV BACKEND_PORT=8000
# Restrict nginx envsubst to ONLY our variable so that native nginx variables
# like $host, $uri, $remote_addr are left untouched.
ENV NGINX_ENVSUBST_FILTER=^BACKEND_PORT$

# Copy built assets
COPY --from=build /app/dist /usr/share/nginx/html

# Drop the template into nginx's built-in envsubst location so ${BACKEND_PORT}
# (and only that variable) is substituted at container start. The resulting
# file is written to /etc/nginx/conf.d/default.conf automatically.
COPY nginx.conf /etc/nginx/templates/default.conf.template
RUN rm -f /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
