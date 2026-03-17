# Assistant Conformité CNJE - ENSAE Junior Études

**Pôle DSI — Outil de conformité et d'export ERP** *Version : 1.0*

## Présentation

L'**Assistant Conformité CNJE** est une application web (développée avec Streamlit) conçue pour accompagner les chefs de projet de l'ENSAE Junior Études dans la préparation et la validation de leurs missions. 

Cet outil garantit que chaque étude respecte scrupuleusement le cadre légal et les directives de la Confédération Nationale des Junior-Entreprises (CNJE) avant d'être intégrée dans l'ERP de la structure (Tomate).

### Fonctionnalités Principales

1. **Vérification automatique de conformité** :
   - Prix plancher par Jour-Étude Homme (JEH).
   - Taux de rémunération minimum par étudiant.
   - Cohérence des dates d'étapes et durée maximale de l'étude.
   - Calcul et intégration des frais de gestion (fee) et de la cotisation économique CNJE (1%).
2. **Génération de contenu assistée par IA** : Création automatique de prompts pour rédiger les textes de la mission (description, détail des étapes, compétences requises).
3. **Export automatisé** : Envoi direct des données validées vers l'ERP **Tomate** via Firebase/Firestore (sans aucune ressaisie manuelle).

---

## Architecture du Projet

L'architecture du projet est pensée pour être simple à maintenir, avec une séparation claire entre la logique de l'interface, les connexions bases de données et les règles métiers.

```text
├── app.py                 # Point d'entrée de l'application (Interface Streamlit)
├── rules_cnje.yaml        # Fichier central de configuration des règles de la CNJE
├── requirements.txt       # Liste des dépendances Python du projet
├── README.md              # Documentation actuelle
└── utils/                 # Dossier contenant la logique métier et les connexions
    ├── firebase_db.py     # Connecteur pour Firebase/Firestore et l'export Tomate
    ├── conformity.py      # Moteur de calcul et de vérification des règles
    └── prompt_gen.py      # Module de génération des prompts IA
