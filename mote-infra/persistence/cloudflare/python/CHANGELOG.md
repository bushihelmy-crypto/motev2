# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project intends to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once its persistence contract stabilizes.

## [Unreleased]

### Changed

- Renamed the package and import namespace to `mote-infra-persistence-cloudflare-python` and `mote_infra_persistence_cloudflare`.
- Split Worker and Durable Object container hosting into `mote-container/cloudflare/python`.
- Retained Cloudflare SQLite schema, serialization, and transaction ownership in this Persistence package.
- Made persistence selection independent of the Cloudflare Container; Port configuration supplies storage only when this backend is selected.
