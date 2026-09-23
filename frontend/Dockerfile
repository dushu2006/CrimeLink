# syntax=docker/dockerfile:1.7
#
# CrimeLink console: build the Vite bundle, then serve it from nginx.  The build
# stage is discarded, so the shipped image contains no toolchain.

FROM node:22.11.0-alpine AS build

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . ./
RUN npm run build

FROM nginx:1.27.3-alpine AS runtime

# Drop the stock site; CrimeLink ships exactly one server block.
RUN rm -f /etc/nginx/conf.d/default.conf

# The server block is a template so the API's address is not baked in: nginx's
# entrypoint renders /etc/nginx/templates/*.template through envsubst before
# starting.  The default is the Compose/Kubernetes service name; a managed
# platform overrides CRIMELINK_API_UPSTREAM with the API service's internal
# address supplied by the deployment environment instead of the console needing
# to know a hostname at build time.
ENV CRIMELINK_API_UPSTREAM=api:8000
COPY nginx.conf /etc/nginx/templates/crimelink.conf.template
COPY --from=build /build/dist /usr/share/nginx/html

RUN mkdir -p /var/cache/nginx /var/log/nginx \
 && chown -R nginx:nginx /var/cache/nginx /var/log/nginx /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
