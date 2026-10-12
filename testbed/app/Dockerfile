FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src
USER node
# ROLE picks the process: collector | worker | api
CMD ["sh", "-c", "exec node src/${ROLE}.js"]
