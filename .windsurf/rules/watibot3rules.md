---
trigger: always_on
---

<coding_guidelines>
- My project's written in python
- This project has its on venv
- Use early returns when possible
- Always add documentation when creating new functions and classes
- ALWAYS REREAD and REANALYZE a file ENTIRELY BEFORE and AFTER EDITING to make sure everything went fine
- When a syntax or identation error occurs REREAD AND REAANALYZE THE ENTIRE FILE BEFORE EDITING AGAIN. The SIMPLEST correction (less lines of code) for syntax and identation is ALWAYS THE BEST.
- Never use python scripts to make changes or additions, unless DIRECTLY instructed or given permission by the user. Use the text change tool so the user always knows and keeps track of what changes have been made.
</coding_guidelines>


1. Before editing **read the entire file** and summarise the intention. It doesn't matter if you have already read it before REREAD IT EVERYTIME YOU ARE ABOUT TO MAKE AN EDIT.
2. If changes touch other files, **analyse cross-file impacts first** and
   report them.
3. Favour the **smallest viable diff**; avoid big refactors.
4. If uncertain, reply **“I’m not sure — please clarify”**.  
   *Do not guess or invent logic.*
5. **Do not break existing project behaviour** or tests.