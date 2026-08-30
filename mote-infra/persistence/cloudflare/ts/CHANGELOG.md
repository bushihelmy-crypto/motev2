# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project intends to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once its persistence contract stabilizes.

## [Unreleased]

### Changed

- Renamed the package to `@mote/infra-persistence-cloudflare` and moved it under `mote-infra/persistence`.
- Split Worker and Durable Object Container hosting into `mote-resource/container/cloudflare/ts`.
- Retained Cloudflare SQLite schema, serialization, and transaction ownership in this Persistence package.
- Made persistence selection independent of the Cloudflare Container; Port configuration supplies storage only when this backend is selected.
