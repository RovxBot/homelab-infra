# syntax=docker/dockerfile:1.28@sha256:bb22d9815c728170f72750f4e5b0d672e06176142e1d602c7e66c050100b7e5b

FROM php:8.5-apache@sha256:974e3a920309308e1690ed607e6cc3061d6906f90cc8d30fe519daa48bccf101

ENV DEBIAN_FRONTEND=noninteractive \
    COMPOSER_ALLOW_SUPERUSER=1

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

WORKDIR /var/www/html
COPY . /var/www/html

WORKDIR /var/www/html/application
RUN --mount=type=cache,target=/root/.composer/cache \
    composer install --no-dev --prefer-dist --no-interaction --optimize-autoloader

WORKDIR /var/www/html
RUN chown -R www-data:www-data /var/www/html

EXPOSE 80