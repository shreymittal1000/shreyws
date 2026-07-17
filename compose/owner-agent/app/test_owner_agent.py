#!/usr/bin/env python3

import unittest

from owner_agent import parse_authentik_groups


class AuthentikGroupParsingTests(unittest.TestCase):
    def test_single_group(self):
        self.assertEqual(parse_authentik_groups("shreyws-owners"), {"shreyws-owners"})

    def test_pipe_separated_groups(self):
        self.assertEqual(
            parse_authentik_groups("authentik Admins|shreyws-owners"),
            {"authentik Admins", "shreyws-owners"},
        )

    def test_pipe_separated_groups_with_whitespace(self):
        self.assertEqual(
            parse_authentik_groups("authentik Admins | shreyws-owners"),
            {"authentik Admins", "shreyws-owners"},
        )

    def test_missing_header(self):
        self.assertEqual(parse_authentik_groups(None), set())
        self.assertEqual(parse_authentik_groups(""), set())

    def test_similarly_named_group_is_not_authorized(self):
        groups = parse_authentik_groups("shreyws-owners-readonly|not-shreyws-owners")
        self.assertNotIn("shreyws-owners", groups)


if __name__ == "__main__":
    unittest.main()
