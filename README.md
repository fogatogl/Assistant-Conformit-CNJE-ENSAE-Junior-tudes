# Assistant Conformité CNJE — ENSAE Junior Études

Système de validation et de génération de prompts IA pour assurer la conformité CNJE des études.

---

## Structure du projet

```
.
├── rules_cnje.yaml      ← Référentiel CNJE (SEUL fichier à modifier pour les règles)
├── rules_engine.py      ← Moteur de validation (lit le YAML, ne contient aucune règle en dur)
├── app.py               ← Interface Streamlit (formulaire multi-étapes)
├── requirements.txt     ← Dépendances Python
└── README.md
```

---

## Installation et lancement

```bash
# 1. Installer les dépendances
pip install -r requirements.txt

# 2. Lancer l'application
streamlit run app.py

# 3. Ouvrir dans le navigateur
# → http://localhost:8501
```

**Déploiement gratuit** : Streamlit Community Cloud  
→ https://streamlit.io/cloud  
→ Connecter le dépôt GitHub, sélectionner `app.py`, déployer.

---

## Parcours utilisateur (6 étapes)

| Étape | Contenu |
|-------|---------|
| 1 — Étude | Nom, domaine, lieu, client, entreprise, signataire |
| 2 — Étapes & JEH | Création des phases de travail, assignation des JEH par étudiant |
| 3 — Intervenants | Récapitulatif automatique des JEH et rémunérations par étudiant |
| 4 — Financier | Prix/JEH, frais de gestion, taux de rémunération, tableau récapitulatif |
| 5 — Validation | Rapport complet CNJE, score de conformité, détail par catégorie |
| 6 — Prompt IA | Génération du Mega-Prompt anonymisé à copier dans Claude/Gemini/ChatGPT |

---

## Mettre à jour une règle CNJE

**Ouvrir `rules_cnje.yaml` et modifier la valeur concernée.**

Exemple — le plancher JEH passe de 200 € à 250 € :

```yaml
# Avant
- id: JEH_PRIX_MIN
  valeur: 200

# Après
- id: JEH_PRIX_MIN
  valeur: 250
  date_maj: "2025-06-01"   ← mettre à jour la date
```

C'est tout. Aucune modification de code Python nécessaire.

**Tracer la modification dans le changelog** (bas du fichier YAML) :

```yaml
changelog:
  - version: "1.1.0"
    date: "2025-06-01"
    auteur: "Prénom NOM"
    modifications:
      - "JEH_PRIX_MIN : plancher relevé à 250 € (nouvelle directive CNJE)"
```

---

## Ajouter une nouvelle règle

Copier le bloc d'une règle existante de même type dans la section appropriée :

```yaml
regles_jeh:
  - id: MA_NOUVELLE_REGLE          # identifiant unique, jamais modifier une fois en prod
    categorie: "Prix par JEH"
    champ_tomate: "p_jeh"
    description: >
      Explication en français de ce que cette règle vérifie.
    valeur: 300
    unite: "€ HT / JEH"
    type_validation: "min"         # min | max | min_calcul | max_ratio | not_null | coherence_dates | unicite | reference
    severite: "bloquant"           # bloquant | avertissement | informatif
    active: true
    ref_cnje: "Art. XX"
    message_erreur: >
      Message affiché à l'utilisateur. Utiliser {valeur_saisie} et {valeur}
      comme placeholders.
    date_maj: "2025-06-01"
```

Puis implémenter le validateur dans `rules_engine.py` si `type_validation` est un nouveau type.  
Pour les types existants (`min`, `max`, `not_null`), le moteur les gère automatiquement.

---

## Architecture — Séparation des responsabilités

```
rules_cnje.yaml       → QUI (les règles, les valeurs, les messages)
                         Modifié par : pôle Qualité, bureau CNJE
rules_engine.py       → COMMENT (lire les règles, calculer, valider)
                         Modifié par : pôle SI uniquement
app.py                → AFFICHAGE (formulaire, navigation, UX)
                         Modifié par : pôle SI uniquement
```

**Principe clé** : un membre du pôle Qualité peut mettre à jour une règle CNJE  
en modifiant uniquement le YAML — sans toucher au code Python.

---

## Connexion avec Tomate

L'export vers Tomate n'est pas encore automatisé (Sprint 3).  
En attendant, le flux manuel est :

1. Valider l'étude dans cet outil (étape 5).
2. Générer le Mega-Prompt (étape 6), coller dans Claude/Gemini, récupérer le texte.
3. Créer l'étude dans Tomate et renseigner manuellement les champs validés.

---

## Tests unitaires

```bash
python rules_engine.py
```

Exécute 7 cas de test couvrant les règles critiques (JEH trop bas, taux rémunération,  
client manquant, dates incohérentes, trop de JEH par étudiant, aucun intervenant).

---

## Référentiel chargé

- Version : voir `meta.version` dans `rules_cnje.yaml`
- Sections : 12 sections de règles métier + configuration Mega-Prompt
- Règles : 31 règles (21 bloquantes, 8 avertissements, 2 informatives)

---

## Sprint 2 — Export vers Tomate (`tomate_bridge.py`)

### Ce que fait le pont

`tomate_bridge.py` envoie automatiquement une étude validée vers Tomate via ses endpoints AJAX existants. Aucune modification de Tomate requise.

**Pipeline ordonné :**
1. Authentification (`POST /Auth/AJAX/SignIn/`) → cookie de session
2. Création de l'entreprise (`POST /Ajax/SaveEntreprise/`) → `entreprise_id`
3. Création du client + signataire (`POST /Ajax/SaveClient/`) → `client_id`
4. Création de l'étude (`POST /Ajax/SaveEtude/`) → `etude_id`, `numero`
5. Envoi des étapes & JEH (`POST /Ajax/SaveEtapes/`) → étapes liées

### Tester la connexion en ligne de commande

```bash
python tomate_bridge.py \
  --url http://votre-tomate.fr \
  --email admin@ensaeje.fr \
  --password votre_mdp \
  --dry-run      # test de connectivité uniquement, sans créer de données
```

Sans `--dry-run` : crée une étude de test (à supprimer manuellement).

### Prérequis Tomate pour l'export

- L'utilisateur admin doit avoir `level >= 2` dans la table `etudiant`
- Les endpoints `/Ajax/*` doivent être accessibles depuis le serveur Streamlit
- Si Tomate est sur un autre domaine : configurer CORS dans `.htaccess`

### Mapping des données

| Champ Streamlit | Endpoint Tomate | Champ Tomate |
|---|---|---|
| `etude_nom` | SaveEtude | `nom` |
| `etude_domaines` (labels) | SaveEtude | `domaines` (IDs) |
| `p_jeh`, `per_rem`, `fee` | SaveEtude | identiques |
| `etude_lieu` (int) | SaveEtude | `lieu` |
| `client.nom/prenom` | SaveClient | `nom`, `prenom` |
| `entreprise.nom` | SaveEntreprise | `nom` |
| `etapes[].nom/details/dates` | SaveEtapes | `nom`, `details`, `date_start`, `date_end` |
| `sEtapes[].etudiant_id + jeh` | SaveEtapes | `etudiant`, `jeh` |

**Note** : les IDs des étudiants (`etudiant_id`) doivent correspondre aux IDs réels dans la base Tomate. Pour le moment, la saisie est manuelle dans le formulaire Streamlit.
