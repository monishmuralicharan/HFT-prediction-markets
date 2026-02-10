# Kalshi HFT Bot Design Specification

## Overview

This specification outlines the design for a high-frequency trading bot targeting prediction markets on Kalshi. The bot focuses on low-risk, high-frequency trades by entering markets at 85%+ probability and exiting at 2% profit.

## Core Strategy

- **Entry Criteria**: Markets with 85%+ probability
- **Profit Target**: 2% above entry price
- **Stop Loss**: 1% below entry price
- **Market Focus**: Prediction markets (high liquidity, fast resolution)

## Document Structure

1. [Architecture Overview](./01-architecture.md) - System components and data flow
2. [Kalshi Integration](./02-kalshi-integration.md) - API and WebSocket details
3. [Trading Strategy](./03-trading-strategy.md) - Entry/exit logic and order management
4. [Risk Management](./04-risk-management.md) - Position sizing, limits, and safeguards
5. [Technical Implementation](./05-technical-implementation.md) - Tech stack and infrastructure
6. [Data Models](./06-data-models.md) - Core data structures
7. [Monitoring & Ops](./07-monitoring-ops.md) - Logging, alerts, and operations
8. [Implementation Roadmap](./08-implementation-roadmap.md) - Development timeline

## Quick Start Guide

See `QUICKSTART.md` in the project root.

## Version History

- v0.1 - Initial design specification for Polymarket (2026-02-05)
- v0.2 - Migrated to Kalshi integration (2026-02-09)
