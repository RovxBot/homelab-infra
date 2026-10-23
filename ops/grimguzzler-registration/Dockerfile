# syntax=docker/dockerfile:1.28@sha256:bb22d9815c728170f72750f4e5b0d672e06176142e1d602c7e66c050100b7e5b

FROM php:8.5.12-apache@sha256:e25ab14ef2cfec3bab9dd66c6cf871691146f428fbd72241b1a2c38fcacf92f5

ENV DEBIAN_FRONTEND=noninteractive \
    COMPOSER_ALLOW_SUPERUSER=1 \
    TEMPLATE=advance

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        unzip \
        libfreetype6-dev \
        libgmp-dev \
        libjpeg62-turbo-dev \
        libxml2-dev \
        libonig-dev \
        libpng-dev \
        libzip-dev \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j"$(nproc)" \
        gd \
        gmp \
        mbstring \
        pdo_mysql \
        soap \
        zip \
    && a2enmod rewrite \
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2.10.3@sha256:af98f42dfff7c68ba8d53c2164fd9fde1087b7d449514baa38c418b1f6bc4bac /usr/bin/composer /usr/local/bin/composer

# Keep application errors out of HTTP responses and restrict request parsing.
RUN cp "$PHP_INI_DIR/php.ini-production" "$PHP_INI_DIR/php.ini" \
    && printf '%s\n' \
        'expose_php=Off' \
        'display_errors=Off' \
        'log_errors=On' \
        'session.use_strict_mode=1' \
        'session.use_only_cookies=1' \
        'session.cookie_httponly=1' \
        'session.cookie_samesite=Lax' \
        'session.upload_progress.enabled=0' \
        'file_uploads=Off' \
        'post_max_size=1M' \
        'max_input_vars=100' \
        'max_input_nesting_level=8' \
        'memory_limit=128M' \
        > "$PHP_INI_DIR/conf.d/registration-security.ini" \
    && printf '%s\n' 'ServerTokens Prod' 'ServerSignature Off' \
        > /etc/apache2/conf-available/registration-security.conf \
    && a2enconf registration-security

WORKDIR /var/www/html
COPY . /var/www/html
# Also defend against builders that do not honor the app's .dockerignore.
RUN rm -rf /var/www/html/.git /var/www/html/.github /var/www/html/.agents /var/www/html/.codex /var/www/html/.aws

# Fail before publishing if the cloned app source predates the Advance redesign.
RUN set -eu; \
    test -s template/advance/tpl/site.php; \
    test -s application/include/realm-stats.php; \
    test -s application/include/raid-catalog.php; \
    for page in home status arena raids guilds pvp play register account support; do \
        test -s "template/advance/tpl/pages/${page}.php"; \
        php -l "template/advance/tpl/pages/${page}.php"; \
    done; \
    for asset in logo tavern play store; do \
        test -s "template/advance/assets/img/grimguzzler/${asset}.webp"; \
    done

WORKDIR /var/www/html/application
RUN --mount=type=cache,target=/root/.composer/cache \
    composer install --no-dev --prefer-dist --no-interaction --optimize-autoloader \
    && composer check-platform-reqs --no-dev \
    && composer audit --locked --no-interaction

WORKDIR /var/www/html
# PHP only needs write access to its session/temp directories, not its source.
RUN find /var/www/html -type d -exec chmod 755 {} + \
    && find /var/www/html -type f -exec chmod 644 {} + \
    && php -r 'if (PHP_VERSION_ID < 80512) { exit(1); }' \
    && apache2ctl -t

EXPOSE 80
