# Agent Leadforms Meta

Automatiseringsagent voor het ophalen en verwerken van Meta (Facebook/Instagram) leadformulierdata.

## Structuur

```
project/
├── blueprints/    # Markdown SOP's — wat en hoe
├── systems/       # Python-scripts — de uitvoering
├── .env           # API-sleutels (nooit committen)
├── claude.md      # Agent-instructies (BOS-framework)
└── README.md      # Dit bestand
```

## Gebruik

1. Vul `.env` in met je Meta API-credentials
2. Raadpleeg een Blueprint in `blueprints/` voor de gewenste taak
3. De agent voert de bijbehorende systems uit
