"""Checkpoints: the regression gate, the commit + record, and rollback (task 17).

A **checkpoint** is a durable, verified task boundary — the point at which a task is not
merely "Auditor-passed" but confirmed finished and robust on the *integrated* live tree
(design §10.1). The three modules here are the backend half of that:

* :mod:`~saltcode.checkpoint.regression` runs the project's FULL suite on the live tree
  inside the container;
* :mod:`~saltcode.checkpoint.checkpoint` makes the git commit and writes the record;
* :mod:`~saltcode.checkpoint.rollback` returns the tree to a named checkpoint.

The *routing* — whether a regression failure is a Builder retry or a FLAG HUMAN — is the
extension's (Task 18.1, REQ-CKP-003). This package computes and reports; it never decides
what the loop does next.
"""
