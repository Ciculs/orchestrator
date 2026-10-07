from __future__ import annotations

import unittest
from pathlib import Path
from ma.skills import list_installed_skills, load_skill_content, build_skills_instruction
from ma.config import load_config, get_systems_mapping, get_role_skills


class TestSkillsIntegration(unittest.TestCase):
    def test_list_installed_skills(self):
        skills = list_installed_skills()
        self.assertIsInstance(skills, list)
        self.assertGreater(len(skills), 0)
        first = skills[0]
        self.assertIn("name", first)
        self.assertIn("path", first)
        self.assertIn("size", first)
        self.assertIn("summary", first)

    def test_load_skill_content(self):
        content = load_skill_content("security-and-hardening")
        self.assertIsInstance(content, str)
        self.assertIn("Security", content)

        missing = load_skill_content("non-existent-skill-xyz")
        self.assertEqual(missing, "")

    def test_build_skills_instruction(self):
        empty = build_skills_instruction([])
        self.assertEqual(empty, "")

        instruction = build_skills_instruction(["security-and-hardening"])
        self.assertIn("SPECIALIZED SKILL GUIDELINES: security-and-hardening", instruction)
        self.assertIn("ASSIGNED DEPARTMENT SKILLS & OPERATIONAL PROTOCOLS", instruction)

    def test_config_roles_have_skills(self):
        cfg = load_config()
        roles = cfg.get("roles", {})
        self.assertIn("critique", roles)
        critique_skills = roles["critique"].get("skills", [])
        self.assertIn("security-and-hardening", critique_skills)

        design_skills = roles["design"].get("skills", [])
        self.assertIn("spec-driven-development", design_skills)

    def test_systems_mapping_injects_skills(self):
        mapping_with = get_systems_mapping(include_skills=True)
        mapping_without = get_systems_mapping(include_skills=False)

        self.assertIn("critique", mapping_with)
        self.assertIn("critique", mapping_without)
        self.assertGreater(len(mapping_with["critique"]), len(mapping_without["critique"]))
        self.assertIn("ASSIGNED DEPARTMENT SKILLS", mapping_with["critique"])


if __name__ == "__main__":
    unittest.main()
