"""Choix des ports : ligne de commande, sinon boitier.json, sinon la harpe."""

import json
from types import SimpleNamespace

from kromabridge import main


def test_ports_come_from_boitier_json(tmp_path, monkeypatch):
    fichier = tmp_path / "boitier.json"
    fichier.write_text(json.dumps({"entree": "kromalaser", "sortie": "ch345"}))
    monkeypatch.setattr(main, "CONFIG_FILE", fichier)
    assert main._ports(SimpleNamespace(entree=None, sortie=None)) == ("kromalaser", "ch345")
    # le fichier l'emporte sur les options du service
    assert main._ports(SimpleNamespace(entree="minifuse", sortie="minifuse")) == ("kromalaser", "ch345")


def test_default_input_is_the_harp(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CONFIG_FILE", tmp_path / "absent.json")
    assert main._ports(SimpleNamespace(entree=None, sortie=None)) == ("kromalaser", None)


def test_without_file_the_command_line_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CONFIG_FILE", tmp_path / "absent.json")
    assert main._ports(SimpleNamespace(entree="x", sortie="y")) == ("x", "y")
