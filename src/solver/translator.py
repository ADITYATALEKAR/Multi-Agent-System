"""Constraint translator -- converts SMT-LIB2 strings to Z3 constraint objects.

Provides Z3Translator (primary) and ConstraintTranslator (backward-compat alias)
for parsing SMT-LIB2 formatted strings into z3 BoolRef constraints.
"""

from __future__ import annotations

import structlog
import z3

logger = structlog.get_logger(__name__)


class TranslationError(Exception):
    """Raised when an SMT-LIB2 string cannot be translated to Z3 constraints."""


class Z3Translator:
    """Translates SMT-LIB2 constraint strings into Z3 BoolRef objects.

    Supports boolean, integer, real, and bitvector constraint types via
    z3.parse_smt2_string().
    """

    def translate(self, smt_lib2: str) -> list[z3.BoolRef]:
        """Parse an SMT-LIB2 string into a list of Z3 BoolRef constraints.

        Args:
            smt_lib2: Constraint expressed in SMT-LIB2 syntax.

        Returns:
            List of z3.BoolRef constraint objects.

        Raises:
            TranslationError: If the input cannot be parsed.
        """
        if not smt_lib2 or not smt_lib2.strip():
            raise TranslationError("Empty SMT-LIB2 input")

        try:
            result = z3.parse_smt2_string(smt_lib2)
            constraints: list[z3.BoolRef] = list(result)
            logger.debug(
                "translated_smt2",
                constraint_count=len(constraints),
                input_length=len(smt_lib2),
            )
            return constraints
        except z3.Z3Exception as exc:
            logger.warning(
                "translation_failed",
                error=str(exc),
                input_preview=smt_lib2[:120],
            )
            raise TranslationError(f"Z3 parse error: {exc}") from exc
        except Exception as exc:
            logger.warning(
                "translation_unexpected_error",
                error=str(exc),
                input_preview=smt_lib2[:120],
            )
            raise TranslationError(f"Unexpected translation error: {exc}") from exc

    def translate_batch(self, constraints: list[str]) -> list[z3.BoolRef]:
        """Translate multiple SMT-LIB2 strings, collecting all constraints.

        Args:
            constraints: List of SMT-LIB2 constraint strings.

        Returns:
            Flat list of all z3.BoolRef constraints from all inputs.

        Raises:
            TranslationError: If any individual constraint fails to parse.
        """
        all_constraints: list[z3.BoolRef] = []
        for i, smt_str in enumerate(constraints):
            try:
                parsed = self.translate(smt_str)
                all_constraints.extend(parsed)
            except TranslationError:
                logger.warning(
                    "batch_translation_item_failed",
                    index=i,
                    input_preview=smt_str[:120] if smt_str else "<empty>",
                )
                raise
        logger.debug(
            "batch_translation_complete",
            input_count=len(constraints),
            total_constraints=len(all_constraints),
        )
        return all_constraints


# Backward compatibility alias
ConstraintTranslator = Z3Translator
