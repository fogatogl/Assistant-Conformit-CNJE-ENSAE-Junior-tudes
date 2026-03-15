"""
rules_engine.py — Moteur de validation CNJE
============================================

Charge rules_cnje.yaml et expose une interface unique :

    engine = RulesEngine("rules_cnje.yaml")
    results = engine.validate(etude_data)   # → liste de ValidationResult

Le moteur est SANS ÉTAT : il ne connaît pas Streamlit, pas de BDD.
Il prend un dict Python, retourne une liste de résultats.
Tout le reste (affichage, formulaire) est dans app.py.

STRUCTURE DE etude_data (dict attendu en entrée) :
{
    "p_jeh": 400,
    "per_rem": 65,
    "fee": 0.0,
    "break_jeh": 0,
    "break_fee": 0.0,
    "numero": 42,
    "date_created": datetime(2025, 1, 1),
    "statut_id": 0,
    "locked": False,
    "has_child": False,
    "etapes": [
        {
            "nom": "Phase 1",
            "details": "...",
            "date_start": datetime(2025, 2, 1),
            "date_end": datetime(2025, 3, 1),
            "sEtapes": [
                {"etudiant_id": 1, "etudiant_nom": "Alice M.", "jeh": 5, "level": 0},
                {"etudiant_id": 2, "etudiant_nom": "Bob D.",   "jeh": 3, "level": 1},
            ]
        }
    ],
    "client":      {"id": 1, "nom": "Dupont", "prenom": "Jean"},  # ou None
    "entreprise":  {"id": 1, "nom": "ACME SA", "siret": "..."},   # ou None
    "signataire":  {"id": 1, "nom": "Martin"},                    # ou None
    "admins":      [{"id": 10, "nom": "Admin A"}],
    "docs":        [{"type_var_name": "convention", "archived": False}],
}
"""

from __future__ import annotations
import yaml
import math
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Types de retour
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    rule_id: str
    severite: str          # "bloquant" | "avertissement" | "informatif"
    categorie: str
    message: str
    ref_cnje: str | None = None
    champ: str | None = None

    @property
    def is_error(self) -> bool:
        return self.severite == "bloquant"

    @property
    def is_warning(self) -> bool:
        return self.severite == "avertissement"

    @property
    def is_info(self) -> bool:
        return self.severite == "informatif"


@dataclass
class ValidationReport:
    results: list[ValidationResult] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationResult]:
        return [r for r in self.results if r.is_error]

    @property
    def warnings(self) -> list[ValidationResult]:
        return [r for r in self.results if r.is_warning]

    @property
    def infos(self) -> list[ValidationResult]:
        return [r for r in self.results if r.is_info]

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    @property
    def by_category(self) -> dict[str, list[ValidationResult]]:
        cats: dict[str, list] = {}
        for r in self.results:
            cats.setdefault(r.categorie, []).append(r)
        return cats


# ---------------------------------------------------------------------------
# Moteur principal
# ---------------------------------------------------------------------------

class RulesEngine:
    """
    Charge le fichier YAML une seule fois à l'instanciation.
    Appeler validate(etude_data) autant de fois que nécessaire.
    """

    def __init__(self, yaml_path: str = "rules_cnje.yaml"):
        with open(yaml_path, "r", encoding="utf-8") as f:
            self._raw = yaml.safe_load(f)
        self._sections = self._index_rules()

    def _index_rules(self) -> dict[str, list[dict]]:
        """Extrait toutes les règles actives, indexées par section."""
        RULE_SECTIONS = [
            "regles_jeh",
            "regles_remuneration",
            "regles_volume_jeh",
            "regles_frais_gestion",
            "regles_cotisation_cnje",
            "regles_tva",
            "regles_dates",
            "regles_intervenants",
            "regles_client",
            "documents_obligatoires",
            "regles_coherence_globale",
        ]
        out = {}
        for section in RULE_SECTIONS:
            rules = self._raw.get(section, [])
            out[section] = [r for r in rules if r.get("active", True)]
        return out

    # ------------------------------------------------------------------
    # Point d'entrée public
    # ------------------------------------------------------------------

    def validate(self, data: dict) -> ValidationReport:
        """Valide un etude_data complet. Retourne un ValidationReport."""
        report = ValidationReport()
        ctx = self._build_context(data)

        # On appelle chaque validateur de section
        self._check_jeh(ctx, report)
        self._check_remuneration(ctx, report)
        self._check_volume_jeh(ctx, report)
        self._check_frais_gestion(ctx, report)
        self._check_dates(ctx, report)
        self._check_intervenants(ctx, report)
        self._check_client(ctx, report)
        self._check_documents(ctx, report)
        self._check_coherence_globale(ctx, report)

        return report

    # ------------------------------------------------------------------
    # Construction du contexte enrichi
    # (calculs dérivés centralisés ici, pas dans chaque validateur)
    # ------------------------------------------------------------------

    def _build_context(self, data: dict) -> dict:
        ctx = dict(data)

        p_jeh    = float(data.get("p_jeh", 0) or 0)
        per_rem  = float(data.get("per_rem", 0) or 0)
        fee      = float(data.get("fee", 0) or 0)
        etapes   = data.get("etapes", []) or []

        # Total JEH toutes étapes
        total_jeh = sum(
            sum(se.get("jeh", 0) for se in (e.get("sEtapes") or []))
            for e in etapes
        )
        ctx["total_jeh"] = total_jeh

        # JEH par étudiant (agrégé sur l'ensemble de l'étude)
        jeh_par_etudiant: dict[int, dict] = {}
        for etape in etapes:
            for se in (etape.get("sEtapes") or []):
                eid = se.get("etudiant_id")
                if eid is None:
                    continue
                if eid not in jeh_par_etudiant:
                    jeh_par_etudiant[eid] = {
                        "nom": se.get("etudiant_nom", f"Étudiant #{eid}"),
                        "level": se.get("level", 0),
                        "total_jeh": 0,
                    }
                jeh_par_etudiant[eid]["total_jeh"] += se.get("jeh", 0)
        ctx["jeh_par_etudiant"] = jeh_par_etudiant

        # Calculs financiers (miroir de Etude.php)
        prix_ht  = total_jeh * p_jeh
        prix_eco = prix_ht * 0.01
        prix_tot = prix_ht + fee + prix_eco
        prix_tva = prix_tot * 0.20
        prix_ttc = prix_tot * 1.20

        ctx["prix_ht"]  = prix_ht
        ctx["prix_eco"] = prix_eco
        ctx["prix_tot"] = prix_tot
        ctx["prix_tva"] = prix_tva
        ctx["prix_ttc"] = prix_ttc

        # Rémunération effective par JEH
        ctx["rem_par_jeh"] = (per_rem / 100.0) * p_jeh

        # Somme totale rémunérations
        somme_rem = 0.0
        for etudiant in jeh_par_etudiant.values():
            somme_rem += (per_rem / 100.0) * p_jeh * etudiant["total_jeh"]
        ctx["somme_remunerations"] = somme_rem

        # Durée de l'étude en semaines
        all_starts = [
            e["date_start"] for e in etapes
            if e.get("date_start") and isinstance(e["date_start"], (datetime, date))
        ]
        all_ends = [
            e["date_end"] for e in etapes
            if e.get("date_end") and isinstance(e["date_end"], (datetime, date))
        ]
        if all_starts and all_ends:
            d_start = min(all_starts)
            d_end   = max(all_ends)
            if isinstance(d_start, datetime): d_start = d_start.date()
            if isinstance(d_end, datetime):   d_end   = d_end.date()
            ctx["etude_date_start"] = d_start
            ctx["etude_date_end"]   = d_end
            delta = (d_end - d_start).days
            ctx["n_week"] = math.ceil(delta / 7) if delta > 0 else 0
        else:
            ctx["etude_date_start"] = None
            ctx["etude_date_end"]   = None
            ctx["n_week"] = None

        return ctx

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_rule(self, section: str, rule_id: str) -> dict | None:
        for r in self._sections.get(section, []):
            if r.get("id") == rule_id:
                return r
        return None

    def _add(self, report: ValidationReport, rule: dict,
             message: str, use_warning_msg: bool = False) -> None:
        """Ajoute un résultat au rapport en choisissant le bon message."""
        if use_warning_msg:
            msg = rule.get("message_avertissement") or message
        else:
            msg = rule.get("message_erreur") or message

        # Nettoyage minimal des placeholders non résolus
        for placeholder in ["{valeur_saisie}", "{valeur}", "{ref_cnje}",
                            "{calcul}", "{nom_etudiant}", "{nom_etape}",
                            "{date_start}", "{date_end}", "{date_created}",
                            "{numero}", "{prix_ht}", "{prix_ttc}",
                            "{somme_remunerations}"]:
            msg = msg.replace(placeholder, "?")

        report.results.append(ValidationResult(
            rule_id=rule["id"],
            severite=rule.get("severite", "avertissement"),
            categorie=rule.get("categorie", "Général"),
            message=msg,
            ref_cnje=rule.get("ref_cnje"),
            champ=rule.get("champ_tomate"),
        ))

    def _fmt(self, rule: dict, message_key: str, replacements: dict) -> str:
        """Formatte un message en remplaçant les placeholders."""
        tmpl = rule.get(message_key) or ""
        for k, v in replacements.items():
            if isinstance(v, float):
                v_str = f"{v:,.2f}".replace(",", " ")
            elif isinstance(v, int):
                v_str = str(v)
            else:
                v_str = str(v) if v is not None else "?"
            tmpl = tmpl.replace("{" + k + "}", v_str)
        # Nettoyer les placeholders restants
        import re
        tmpl = re.sub(r"\{[^}]+\}", "?", tmpl)
        return tmpl.strip()

    # ------------------------------------------------------------------
    # Validateurs par section
    # ------------------------------------------------------------------

    def _check_jeh(self, ctx: dict, report: ValidationReport) -> None:
        p_jeh   = float(ctx.get("p_jeh", 0) or 0)
        b_jeh   = float(ctx.get("break_jeh", 0) or 0)
        section = "regles_jeh"

        # JEH_PRIX_MIN
        r = self._get_rule(section, "JEH_PRIX_MIN")
        if r and p_jeh < r["valeur"]:
            msg = self._fmt(r, "message_erreur", {
                "valeur_saisie": p_jeh, "valeur": r["valeur"], "ref_cnje": r.get("ref_cnje","")
            })
            report.results.append(ValidationResult(
                rule_id=r["id"], severite=r["severite"],
                categorie=r["categorie"], message=msg,
                ref_cnje=r.get("ref_cnje"), champ="p_jeh"))

        # JEH_PRIX_MAX
        r = self._get_rule(section, "JEH_PRIX_MAX")
        if r and p_jeh > r["valeur"]:
            msg = self._fmt(r, "message_avertissement", {
                "valeur_saisie": p_jeh, "valeur": r["valeur"], "ref_cnje": r.get("ref_cnje","")
            })
            report.results.append(ValidationResult(
                rule_id=r["id"], severite=r["severite"],
                categorie=r["categorie"], message=msg,
                ref_cnje=r.get("ref_cnje"), champ="p_jeh"))

        # JEH_PRIX_AVENANT_MIN
        r = self._get_rule(section, "JEH_PRIX_AVENANT_MIN")
        if r and b_jeh > 0 and p_jeh < r["valeur"]:
            msg = self._fmt(r, "message_erreur", {
                "valeur_saisie": p_jeh, "valeur": r["valeur"], "ref_cnje": r.get("ref_cnje","")
            })
            report.results.append(ValidationResult(
                rule_id=r["id"], severite=r["severite"],
                categorie=r["categorie"], message=msg,
                ref_cnje=r.get("ref_cnje"), champ="break_jeh"))

    def _check_remuneration(self, ctx: dict, report: ValidationReport) -> None:
        per_rem = float(ctx.get("per_rem", 0) or 0)
        section = "regles_remuneration"

        # REM_TAUX_MIN
        r = self._get_rule(section, "REM_TAUX_MIN")
        if r and per_rem < r["valeur"]:
            msg = self._fmt(r, "message_erreur", {
                "valeur_saisie": per_rem, "valeur": r["valeur"], "ref_cnje": r.get("ref_cnje","")
            })
            report.results.append(ValidationResult(
                rule_id=r["id"], severite=r["severite"],
                categorie=r["categorie"], message=msg,
                ref_cnje=r.get("ref_cnje"), champ="per_rem"))

        # REM_TAUX_COHERENCE
        r = self._get_rule(section, "REM_TAUX_COHERENCE")
        if r and per_rem > r["valeur"]:
            marge = round(100 - per_rem - 1, 1)
            msg = self._fmt(r, "message_avertissement", {
                "valeur_saisie": per_rem, "valeur": r["valeur"],
                "calcul": marge
            })
            report.results.append(ValidationResult(
                rule_id=r["id"], severite=r["severite"],
                categorie=r["categorie"], message=msg,
                ref_cnje=r.get("ref_cnje"), champ="per_rem"))

        # REM_MONTANT_MIN_PAR_JEH
        r = self._get_rule(section, "REM_MONTANT_MIN_PAR_JEH")
        if r:
            rem_par_jeh = ctx.get("rem_par_jeh", 0)
            if rem_par_jeh < r["valeur"]:
                msg = self._fmt(r, "message_erreur", {
                    "calcul": round(rem_par_jeh, 2), "valeur": r["valeur"],
                    "ref_cnje": r.get("ref_cnje","")
                })
                report.results.append(ValidationResult(
                    rule_id=r["id"], severite=r["severite"],
                    categorie=r["categorie"], message=msg,
                    ref_cnje=r.get("ref_cnje"), champ="p_jeh + per_rem"))

    def _check_volume_jeh(self, ctx: dict, report: ValidationReport) -> None:
        section   = "regles_volume_jeh"
        total_jeh = ctx.get("total_jeh", 0)
        etapes    = ctx.get("etapes", []) or []

        # JEH_MIN_PAR_ETUDE
        r = self._get_rule(section, "JEH_MIN_PAR_ETUDE")
        if r and total_jeh < r["valeur"]:
            msg = self._fmt(r, "message_erreur", {
                "valeur_saisie": total_jeh, "valeur": r["valeur"],
                "ref_cnje": r.get("ref_cnje","")
            })
            report.results.append(ValidationResult(
                rule_id=r["id"], severite=r["severite"],
                categorie=r["categorie"], message=msg,
                ref_cnje=r.get("ref_cnje")))

        # JEH_MAX_PAR_ETUDE_TOTAL
        r = self._get_rule(section, "JEH_MAX_PAR_ETUDE_TOTAL")
        if r and total_jeh > r["valeur"]:
            msg = self._fmt(r, "message_avertissement", {
                "valeur_saisie": total_jeh, "valeur": r["valeur"],
                "ref_cnje": r.get("ref_cnje","")
            })
            report.results.append(ValidationResult(
                rule_id=r["id"], severite=r["severite"],
                categorie=r["categorie"], message=msg,
                ref_cnje=r.get("ref_cnje")))

        # JEH_MAX_PAR_ETUDIANT_PAR_ETUDE
        r = self._get_rule(section, "JEH_MAX_PAR_ETUDIANT_PAR_ETUDE")
        if r:
            for eid, info in ctx.get("jeh_par_etudiant", {}).items():
                if info["total_jeh"] > r["valeur"]:
                    msg = self._fmt(r, "message_erreur", {
                        "nom_etudiant": info["nom"],
                        "valeur_saisie": info["total_jeh"],
                        "valeur": r["valeur"],
                        "ref_cnje": r.get("ref_cnje","")
                    })
                    report.results.append(ValidationResult(
                        rule_id=r["id"], severite=r["severite"],
                        categorie=r["categorie"], message=msg,
                        ref_cnje=r.get("ref_cnje")))

        # JEH_MIN_PAR_ETAPE
        r = self._get_rule(section, "JEH_MIN_PAR_ETAPE")
        if r:
            for etape in etapes:
                n_jeh_etape = sum(se.get("jeh", 0) for se in (etape.get("sEtapes") or []))
                if n_jeh_etape < r["valeur"]:
                    msg = self._fmt(r, "message_erreur", {
                        "nom_etape": etape.get("nom", "?"),
                        "valeur": r["valeur"]
                    })
                    report.results.append(ValidationResult(
                        rule_id=r["id"], severite=r["severite"],
                        categorie=r["categorie"], message=msg,
                        ref_cnje=r.get("ref_cnje")))

        # JEH_ETUDIANT_MEME_ETAPE_DOUBLONS
        r = self._get_rule(section, "JEH_ETUDIANT_MEME_ETAPE_DOUBLONS")
        if r:
            for etape in etapes:
                seen: set[int] = set()
                for se in (etape.get("sEtapes") or []):
                    eid = se.get("etudiant_id")
                    if eid is not None:
                        if eid in seen:
                            msg = self._fmt(r, "message_erreur", {
                                "nom_etudiant": se.get("etudiant_nom", f"#{eid}"),
                                "nom_etape": etape.get("nom", "?")
                            })
                            report.results.append(ValidationResult(
                                rule_id=r["id"], severite=r["severite"],
                                categorie=r["categorie"], message=msg,
                                ref_cnje=r.get("ref_cnje")))
                            break
                        seen.add(eid)

    def _check_frais_gestion(self, ctx: dict, report: ValidationReport) -> None:
        section = "regles_frais_gestion"
        fee     = float(ctx.get("fee", 0) or 0)
        prix_ht = ctx.get("prix_ht", 0)

        # FEE_NEGATIF
        r = self._get_rule(section, "FEE_NEGATIF")
        if r and fee < 0:
            msg = self._fmt(r, "message_erreur", {"valeur_saisie": fee})
            report.results.append(ValidationResult(
                rule_id=r["id"], severite=r["severite"],
                categorie=r["categorie"], message=msg,
                ref_cnje=r.get("ref_cnje"), champ="fee"))

        # FEE_TAUX_MAX
        r = self._get_rule(section, "FEE_TAUX_MAX")
        if r and prix_ht > 0:
            ratio = (fee / prix_ht) * 100
            if ratio > r["valeur"]:
                msg = self._fmt(r, "message_avertissement", {
                    "valeur_saisie": fee,
                    "calcul": round(ratio, 1),
                    "valeur": r["valeur"],
                    "ref_cnje": r.get("ref_cnje","")
                })
                report.results.append(ValidationResult(
                    rule_id=r["id"], severite=r["severite"],
                    categorie=r["categorie"], message=msg,
                    ref_cnje=r.get("ref_cnje"), champ="fee"))

    def _check_dates(self, ctx: dict, report: ValidationReport) -> None:
        section = "regles_dates"
        etapes  = ctx.get("etapes", []) or []

        # DATE_DEBUT_AVANT_FIN (par étape)
        r = self._get_rule(section, "DATE_DEBUT_AVANT_FIN")
        if r:
            for etape in etapes:
                ds = etape.get("date_start")
                de = etape.get("date_end")
                if ds and de:
                    if isinstance(ds, datetime): ds = ds.date()
                    if isinstance(de, datetime): de = de.date()
                    if ds >= de:
                        msg = self._fmt(r, "message_erreur", {
                            "nom_etape": etape.get("nom","?"),
                            "date_start": str(ds),
                            "date_end":   str(de)
                        })
                        report.results.append(ValidationResult(
                            rule_id=r["id"], severite=r["severite"],
                            categorie=r["categorie"], message=msg,
                            ref_cnje=r.get("ref_cnje")))

        # DATE_ETAPES_COHERENCE_ETUDE (global)
        r = self._get_rule(section, "DATE_ETAPES_COHERENCE_ETUDE")
        if r:
            d_start = ctx.get("etude_date_start")
            d_end   = ctx.get("etude_date_end")
            if d_start and d_end and d_end <= d_start:
                msg = self._fmt(r, "message_erreur", {
                    "date_start": str(d_start), "date_end": str(d_end)
                })
                report.results.append(ValidationResult(
                    rule_id=r["id"], severite=r["severite"],
                    categorie=r["categorie"], message=msg,
                    ref_cnje=r.get("ref_cnje")))

        # DUREE_MAX_ETUDE
        r = self._get_rule(section, "DUREE_MAX_ETUDE")
        if r:
            n_week = ctx.get("n_week")
            if n_week is not None and n_week > r["valeur"]:
                msg = self._fmt(r, "message_avertissement", {
                    "valeur_saisie": n_week,
                    "valeur": r["valeur"],
                    "ref_cnje": r.get("ref_cnje","")
                })
                report.results.append(ValidationResult(
                    rule_id=r["id"], severite=r["severite"],
                    categorie=r["categorie"], message=msg,
                    ref_cnje=r.get("ref_cnje")))

        # DATE_CREATION_FUTURE
        r = self._get_rule(section, "DATE_CREATION_FUTURE")
        if r:
            dc = ctx.get("date_created")
            if dc:
                if isinstance(dc, datetime): dc = dc.date()
                today = date.today()
                if dc > today:
                    msg = self._fmt(r, "message_erreur", {"date_created": str(dc)})
                    report.results.append(ValidationResult(
                        rule_id=r["id"], severite=r["severite"],
                        categorie=r["categorie"], message=msg,
                        ref_cnje=r.get("ref_cnje")))

    def _check_intervenants(self, ctx: dict, report: ValidationReport) -> None:
        section             = "regles_intervenants"
        jeh_par_etudiant    = ctx.get("jeh_par_etudiant", {})
        etapes              = ctx.get("etapes", []) or []
        n_intervenants_actifs = len(jeh_par_etudiant)

        # INTERVENANTS_MIN
        r = self._get_rule(section, "INTERVENANTS_MIN")
        if r and n_intervenants_actifs < r["valeur"]:
            msg = self._fmt(r, "message_erreur", {
                "valeur": r["valeur"], "ref_cnje": r.get("ref_cnje","")
            })
            report.results.append(ValidationResult(
                rule_id=r["id"], severite=r["severite"],
                categorie=r["categorie"], message=msg,
                ref_cnje=r.get("ref_cnje")))

        # INTERVENANTS_STATUT_ETUDIANT
        r = self._get_rule(section, "INTERVENANTS_STATUT_ETUDIANT")
        if r:
            for info in jeh_par_etudiant.values():
                if info.get("level", 0) > r["valeur"]:
                    msg = self._fmt(r, "message_erreur", {
                        "valeur": r["valeur"], "ref_cnje": r.get("ref_cnje","")
                    })
                    report.results.append(ValidationResult(
                        rule_id=r["id"], severite=r["severite"],
                        categorie=r["categorie"], message=msg,
                        ref_cnje=r.get("ref_cnje")))
                    break

        # INTERVENANTS_MAX_PAR_ETAPE
        r = self._get_rule(section, "INTERVENANTS_MAX_PAR_ETAPE")
        if r:
            for etape in etapes:
                n = len(set(
                    se.get("etudiant_id") for se in (etape.get("sEtapes") or [])
                    if se.get("etudiant_id") is not None
                ))
                if n > r["valeur"]:
                    msg = self._fmt(r, "message_avertissement", {
                        "nom_etape": etape.get("nom","?"),
                        "valeur_saisie": n, "valeur": r["valeur"]
                    })
                    report.results.append(ValidationResult(
                        rule_id=r["id"], severite=r["severite"],
                        categorie=r["categorie"], message=msg,
                        ref_cnje=r.get("ref_cnje")))

    def _check_client(self, ctx: dict, report: ValidationReport) -> None:
        section = "regles_client"

        checks = [
            ("CLIENT_OBLIGATOIRE",    "client",     "message_erreur"),
            ("ENTREPRISE_OBLIGATOIRE","entreprise",  "message_erreur"),
            ("SIGNATAIRE_OBLIGATOIRE_POUR_CONVENTION","signataire","message_erreur"),
        ]
        for rule_id, field_name, msg_key in checks:
            r = self._get_rule(section, rule_id)
            if r and not ctx.get(field_name):
                msg = self._fmt(r, msg_key, {"ref_cnje": r.get("ref_cnje","")})
                report.results.append(ValidationResult(
                    rule_id=r["id"], severite=r["severite"],
                    categorie=r["categorie"], message=msg,
                    ref_cnje=r.get("ref_cnje"), champ=field_name))

    def _check_documents(self, ctx: dict, report: ValidationReport) -> None:
        section    = "documents_obligatoires"
        statut_id  = ctx.get("statut_id", 0)
        docs       = ctx.get("docs", []) or []

        # DOC_CONVENTION_AVANT_DEMARRAGE
        r = self._get_rule(section, "DOC_CONVENTION_AVANT_DEMARRAGE")
        if r and statut_id >= r.get("statut_declencheur", 4):
            has_convention = any(
                d.get("type_var_name") == r.get("type_doc_requis", "convention")
                for d in docs
            )
            if not has_convention:
                msg = self._fmt(r, "message_erreur", {"ref_cnje": r.get("ref_cnje","")})
                report.results.append(ValidationResult(
                    rule_id=r["id"], severite=r["severite"],
                    categorie=r["categorie"], message=msg,
                    ref_cnje=r.get("ref_cnje")))

        # DOC_AVENANT_AVANT_MODIFICATION
        r = self._get_rule(section, "DOC_AVENANT_AVANT_MODIFICATION")
        if r:
            locked   = ctx.get("locked", False)
            has_child= ctx.get("has_child", False)
            if locked and not has_child:
                msg = self._fmt(r, "message_avertissement", {"ref_cnje": r.get("ref_cnje","")})
                report.results.append(ValidationResult(
                    rule_id=r["id"], severite=r["severite"],
                    categorie=r["categorie"], message=msg,
                    ref_cnje=r.get("ref_cnje")))

    def _check_coherence_globale(self, ctx: dict, report: ValidationReport) -> None:
        section = "regles_coherence_globale"

        # PRIX_TTC_COHERENCE
        r = self._get_rule(section, "PRIX_TTC_COHERENCE")
        if r:
            prix_ht  = ctx.get("prix_ht", 0)
            prix_ttc = ctx.get("prix_ttc", 0)
            if prix_ht > 0 and prix_ttc <= prix_ht:
                msg = self._fmt(r, "message_erreur", {
                    "prix_ttc": round(prix_ttc, 2),
                    "prix_ht":  round(prix_ht, 2)
                })
                report.results.append(ValidationResult(
                    rule_id=r["id"], severite=r["severite"],
                    categorie=r["categorie"], message=msg,
                    ref_cnje=r.get("ref_cnje")))

        # REMUNERATION_TOTALE_SOUS_PRIX_HT
        r = self._get_rule(section, "REMUNERATION_TOTALE_SOUS_PRIX_HT")
        if r:
            prix_ht      = ctx.get("prix_ht", 0)
            somme_rem    = ctx.get("somme_remunerations", 0)
            if prix_ht > 0 and somme_rem > prix_ht:
                msg = self._fmt(r, "message_erreur", {
                    "somme_remunerations": round(somme_rem, 2),
                    "prix_ht": round(prix_ht, 2),
                    "ref_cnje": r.get("ref_cnje","")
                })
                report.results.append(ValidationResult(
                    rule_id=r["id"], severite=r["severite"],
                    categorie=r["categorie"], message=msg,
                    ref_cnje=r.get("ref_cnje")))


# ---------------------------------------------------------------------------
# Générateur de Mega-Prompts
# ---------------------------------------------------------------------------

class MegaPromptGenerator:
    """
    Génère les prompts IA à partir d'une étude VALIDÉE.
    Ne doit être appelé qu'après engine.validate(data).is_valid == True.
    """

    ANONYMIZED = "[CONFIDENTIEL]"

    def __init__(self, yaml_path: str = "rules_cnje.yaml"):
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        cfg = raw.get("mega_prompt_config", {})
        self._excluded  = set(cfg.get("champs_exclus_du_prompt", {}).get("liste", []))
        self._templates = {
            t["id"]: t for t in cfg.get("templates_prompt", [])
        }

    def generate(self, data: dict, template_id: str = "PROMPT_DESCRIPTION_MISSION") -> str:
        tmpl = self._templates.get(template_id)
        if not tmpl:
            raise ValueError(f"Template inconnu : {template_id}")

        etapes = data.get("etapes", []) or []
        domaines = data.get("domaines_labels", []) or []
        lieu_label = data.get("lieu_label", "Non précisé")

        etapes_bloc = ""
        for i, e in enumerate(etapes, 1):
            n_jeh = sum(se.get("jeh", 0) for se in (e.get("sEtapes") or []))
            etapes_bloc += (
                f"  Étape {i} : {e.get('nom','?')} ({n_jeh} JEH)\n"
                f"    Détails : {e.get('details','') or 'À préciser'}\n"
            )

        instructions = tmpl.get("instructions_ia","").replace(
            "{longueur_cible}", tmpl.get("longueur_cible","?")
        )

        prompt = f"""
════════════════════════════════════════════════════════════
PROMPT GÉNÉRÉ PAR LE SYSTÈME DE CONFORMITÉ CNJE — ENSAE JE
Template : {tmpl.get('usage','?')}
════════════════════════════════════════════════════════════

[CONTEXTE DE LA MISSION — NE PAS MODIFIER CES DONNÉES]

Domaine(s) : {', '.join(domaines) if domaines else 'Non précisé'}
Lieu d'intervention : {lieu_label}
Nombre d'étapes : {len(etapes)}
Volume total : {sum(sum(se.get('jeh',0) for se in (e.get('sEtapes') or [])) for e in etapes)} JEH

Étapes de la mission :
{etapes_bloc.rstrip()}

But actuel (à améliorer) :
  {data.get('but','') or '[Champ vide — à rédiger]'}

Spécifications actuelles (à améliorer) :
  {data.get('specifications','') or '[Champ vide — à rédiger]'}

[INSTRUCTION POUR L'IA]

{instructions}

[CONTRAINTES IMPÉRATIVES]
- Ne pas inventer de chiffres, de montants, ni de noms de personnes.
- Ne pas modifier les noms d'étapes ni les volumes de JEH.
- Ne pas inclure d'informations sur le client ou l'entreprise cliente.
- Respecter le registre professionnel (pas de jargon marketing excessif).

════════════════════════════════════════════════════════════
""".strip()
        return prompt

    def generate_all(self, data: dict) -> dict[str, str]:
        """Génère tous les prompts disponibles pour une étude."""
        return {tid: self.generate(data, tid) for tid in self._templates}


# ---------------------------------------------------------------------------
# Tests unitaires intégrés (python rules_engine.py pour lancer)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import date

    print("=" * 60)
    print("TESTS UNITAIRES — RulesEngine")
    print("=" * 60)

    engine = RulesEngine("rules_cnje.yaml")

    # ----- Cas 1 : étude valide -----
    data_ok = {
        "p_jeh": 400, "per_rem": 65, "fee": 200.0,
        "break_jeh": 0, "break_fee": 0,
        "statut_id": 0, "locked": False, "has_child": False,
        "date_created": date.today(),
        "client":     {"id": 1}, "entreprise": {"id": 1}, "signataire": {"id": 1},
        "docs": [], "admins": [],
        "etapes": [{
            "nom": "Analyse",
            "details": "Analyse des données",
            "date_start": date(2025, 3, 1),
            "date_end":   date(2025, 4, 1),
            "sEtapes": [
                {"etudiant_id": 1, "etudiant_nom": "Alice", "jeh": 10, "level": 0},
                {"etudiant_id": 2, "etudiant_nom": "Bob",   "jeh": 5,  "level": 0},
            ]
        }]
    }
    r = engine.validate(data_ok)
    assert r.is_valid, f"Cas 1 ÉCHOUÉ : {[e.message for e in r.errors]}"
    print("[OK] Cas 1 — étude valide : aucune erreur bloquante")

    # ----- Cas 2 : prix JEH trop bas -----
    data_jeh_bas = dict(data_ok, p_jeh=100)
    r = engine.validate(data_jeh_bas)
    ids = [e.rule_id for e in r.errors]
    assert "JEH_PRIX_MIN" in ids, "Cas 2 : JEH_PRIX_MIN non détecté"
    print(f"[OK] Cas 2 — JEH trop bas ({len(r.errors)} erreur(s)) : {ids}")

    # ----- Cas 3 : taux rémunération trop bas -----
    data_rem_bas = dict(data_ok, per_rem=10)
    r = engine.validate(data_rem_bas)
    ids = [e.rule_id for e in r.errors]
    assert "REM_TAUX_MIN" in ids, "Cas 3 : REM_TAUX_MIN non détecté"
    print(f"[OK] Cas 3 — Rémunération trop basse : {ids}")

    # ----- Cas 4 : pas de client -----
    data_no_client = dict(data_ok, client=None)
    r = engine.validate(data_no_client)
    ids = [e.rule_id for e in r.errors]
    assert "CLIENT_OBLIGATOIRE" in ids
    print(f"[OK] Cas 4 — Pas de client : {ids}")

    # ----- Cas 5 : dates incohérentes -----
    data_dates = dict(data_ok)
    data_dates["etapes"] = [{
        "nom": "Phase",
        "date_start": date(2025, 5, 1),
        "date_end":   date(2025, 4, 1),  # fin AVANT début
        "sEtapes": [{"etudiant_id": 1, "etudiant_nom": "Alice", "jeh": 5, "level": 0}]
    }]
    r = engine.validate(data_dates)
    ids = [e.rule_id for e in r.errors]
    assert "DATE_DEBUT_AVANT_FIN" in ids
    print(f"[OK] Cas 5 — Dates incohérentes : {ids}")

    # ----- Cas 6 : trop de JEH par étudiant -----
    data_jeh_max = dict(data_ok)
    data_jeh_max["etapes"] = [{
        "nom": "Phase",
        "date_start": date(2025, 3, 1),
        "date_end":   date(2025, 4, 1),
        "sEtapes": [{"etudiant_id": 1, "etudiant_nom": "Alice", "jeh": 50, "level": 0}]
    }]
    r = engine.validate(data_jeh_max)
    ids = [e.rule_id for e in r.errors]
    assert "JEH_MAX_PAR_ETUDIANT_PAR_ETUDE" in ids
    print(f"[OK] Cas 6 — Trop de JEH / étudiant : {ids}")

    # ----- Cas 7 : aucun intervenant -----
    data_no_ed = dict(data_ok)
    data_no_ed["etapes"] = [{
        "nom": "Phase", "date_start": date(2025,3,1), "date_end": date(2025,4,1),
        "sEtapes": []
    }]
    r = engine.validate(data_no_ed)
    ids = [e.rule_id for e in r.errors]
    assert "INTERVENANTS_MIN" in ids
    print(f"[OK] Cas 7 — Aucun intervenant : {ids}")

    # ----- Résumé -----
    print()
    print("Tous les tests ont passé.")

    # ----- Test Mega-Prompt -----
    print()
    print("=" * 60)
    print("TEST — MegaPromptGenerator")
    print("=" * 60)
    gen = MegaPromptGenerator("rules_cnje.yaml")
    prompt = gen.generate({**data_ok,
        "but": "Analyse des données de vente",
        "specifications": "Modèle prédictif en Python",
        "domaines_labels": ["Machine Learning", "Statistique descriptive"],
        "lieu_label": "À l'ENSAE"
    })
    print(prompt[:600], "...")
    print("\n[OK] Mega-Prompt généré avec succès.")
