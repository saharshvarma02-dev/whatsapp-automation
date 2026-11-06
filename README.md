# Self-Hosted WhatsApp Automation Engine

A self-hosted automation system that connects WhatsApp Web to an AI-powered backend, enabling automated message processing, intelligent response generation, conversation handling, and activity logging.

The project combines browser automation, a Python-based API, large language model integration, and persistent storage to create a modular WhatsApp automation pipeline.

## Overview

The Self-Hosted WhatsApp Automation Engine is designed to automate incoming WhatsApp message handling through an intelligent backend.

It uses browser automation to interact with WhatsApp Web, forwards incoming messages to a FastAPI service, processes them using an AI model, and delivers generated responses back through the WhatsApp interface.

The system also incorporates message filtering, priority handling, guardrail mechanisms, database persistence, and Discord-based activity logging.

Rather than relying on a fully managed cloud automation platform, the architecture is designed around components that can be operated and controlled by the system owner.

## Key Features

- **WhatsApp Web Automation:** Uses Puppeteer to interact with an authenticated WhatsApp Web session.
- **Automated Message Processing:** Detects incoming messages and forwards relevant content to the backend.
- **AI-Powered Responses:** Integrates Google Gemini 2.5 Flash for structured message classification and response generation.
- **Message Filtering:** Filters outgoing messages and irrelevant system events to prevent unnecessary processing.
- **Priority Handling:** Supports message prioritisation and decision-making through backend processing logic.
- **Guardrail Mechanisms:** Applies predefined checks before processing and responding to messages.
- **Persistent Storage:** Uses SQLite to maintain message records and conversation-related data.
- **Discord Logging:** Supports
