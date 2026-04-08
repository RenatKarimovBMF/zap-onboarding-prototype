# Zap Customer Onboarding Prototype

## Overview
This project is a prototype for automating onboarding of a new business client for Zap.

The system:
- scans a client website (simulation only)
- scans an external directory listing (simulating a Dapei Zahav mini-site)
- extracts relevant business information
- creates a structured client card
- generates a personalized onboarding call script
- generates an onboarding email draft
- saves the result into a local CRM simulation by SQL!

Style:
I decided to use more funny and friendly style:
![alt text](image.png)

And the clients page will look like this:
![alt text](image-1.png)

![alt text](image-2.png)
## Goal
To show that I can work with ai tools and create fast AI agent solutions!

## Main Flow
1. The user enters:
   - the main client website page (Clients site)
   - the external listing page (Dapei Zahav)
2. The system crawls internal HTML pages from the main website with Ai agent with API key OR
with a working DEMO like it is on git!
3. It extracts visible text from the website and external listing
4. It builds a structured onboarding record
5. It generates:
   - client card
   - onboarding call script
   - onboarding email draft
   (All saved in SQL db that plays the role of CRM)
6. It stores the outputs in:
   - local SQLite CRM simulation
   - JSON payload
   - text email draft

## Modes

### 1. Demo Mode
If no API key is provided, the app runs in demo mode.

In demo mode, the system performs local rule-based extraction from the provided HTML/text, including:
- phone
- email
- business name
- service area
- opening hours
- business type
- services/categories

This allows the reviewer to run the prototype without external setup.

### 2. Live AI Mode
If an `OPENROUTER_API_KEY` environment variable is provided, the app uses live AI extraction.
Every possible page related to Ai agents strongly suggested to keep this key a secret so a key will be provided via email if needed!

## Technologies Used
- Python
- Tkinter
- SQLite
- OpenAI-compatible API client
- Pillow

## Files Generated During Run
- `crm_demo.db` – local CRM simulation database
- `crm_payload.json` – structured output payload
- `email_draft.txt` – onboarding email draft

## How to Get the Project

### Option 1 – Download ZIP
1. Open the GitHub repository page
2. Click **Code**
3. Click **Download ZIP**
4. Extract the ZIP to a local folder
5. Open that folder in terminal or VS Code

### Option 2 – Clone with Git
```bash
git clone https://github.com/RenatKarimovBMF/zap-onboarding-prototype.git
cd zap-onboarding-prototype