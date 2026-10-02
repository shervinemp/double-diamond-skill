# Starter prompts

## Should run the full pipeline (ambiguous, costly to get wrong)

1. "Set up CI/CD for this project."
2. "We need to add multi-tenancy to our app."
3. "Write a launch announcement for our new product, a pocket-sized label printer for makers."
4. "Help me decide whether to build or buy an internal search tool for a 40-person company."
5. "Plan the migration from our monolith to services."
6. "Should we prioritize shipping the redesign this quarter or fixing the reliability issues first?" (a values trade-off: the judge should flag that only you can settle it)

## Recommendation tasks (should use the vetting path and ask almost nothing)

7. "Where can I print and bind my immigration forms near Austin, Texas?"
   Check: compares real prices, looks beyond one platform's ratings, checks the business exists, flags the privacy of the documents, recommends protected payment, and states what was and wasn't verified.
8. "Which moving company should I use for a cross-country move from Boston to Seattle?"
9. "Find me a contractor to redo my bathroom in Denver."

## Should skip itself (clear and small), as a check that triage works

10. "In this snippet, rename the variable x to count: `x = 0; x += 1; print(x)`"
11. "How do I format a date in Python?"
12. "Fix the typo in this heading: `# Instalation`"

## Edge cases

13. A request that is already fully specified but large. It should skip asking and go straight to the plan.
