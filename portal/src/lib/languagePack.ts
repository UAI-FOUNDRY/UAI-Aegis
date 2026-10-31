/**
 * Languages engineering students (CSE, AI & ML, Data Science) meet in lab courses,
 * as rows for POST /api/v1/admin/languages.
 *
 * judge0_id values are from Judge0 CE 1.13.x (the version in docker-compose.yml).
 * After adding them, check every id against your Judge0 server:
 *     cd backend && python -m app.cli check-judge0
 * monaco_id is the student editor's syntax mode ("plaintext" where Monaco has none).
 */
export type LanguageSpec = {
  key: string
  name: string
  judge0_id: number
  file_name: string
  monaco_id: string
  time_multiplier: number
  /** Why it is in the pack (shown to the admin). */
  course: string
}

export const ENGINEERING_LANGUAGES: LanguageSpec[] = [
  // Core programming, data structures, OOP
  { key: 'c', name: 'C', judge0_id: 50, file_name: 'main.c', monaco_id: 'c', time_multiplier: 1.0, course: 'Programming, DSA, OS' },
  { key: 'cpp', name: 'C++', judge0_id: 54, file_name: 'main.cpp', monaco_id: 'cpp', time_multiplier: 1.0, course: 'DSA, OOP, competitive programming' },
  { key: 'java', name: 'Java', judge0_id: 62, file_name: 'Main.java', monaco_id: 'java', time_multiplier: 2.0, course: 'OOP, DSA' },
  { key: 'python3', name: 'Python 3', judge0_id: 71, file_name: 'main.py', monaco_id: 'python', time_multiplier: 3.0, course: 'Programming, AI, ML, DS' },
  { key: 'csharp', name: 'C#', judge0_id: 51, file_name: 'Main.cs', monaco_id: 'csharp', time_multiplier: 2.0, course: 'OOP, .NET' },
  { key: 'kotlin', name: 'Kotlin', judge0_id: 78, file_name: 'Main.kt', monaco_id: 'kotlin', time_multiplier: 2.0, course: 'Mobile development' },
  { key: 'go', name: 'Go', judge0_id: 60, file_name: 'main.go', monaco_id: 'go', time_multiplier: 1.5, course: 'Systems, cloud' },
  { key: 'rust', name: 'Rust', judge0_id: 73, file_name: 'main.rs', monaco_id: 'rust', time_multiplier: 1.0, course: 'Systems programming' },
  // Web
  { key: 'javascript', name: 'JavaScript (Node.js)', judge0_id: 63, file_name: 'main.js', monaco_id: 'javascript', time_multiplier: 2.0, course: 'Web technologies' },
  { key: 'typescript', name: 'TypeScript', judge0_id: 74, file_name: 'main.ts', monaco_id: 'typescript', time_multiplier: 2.0, course: 'Web technologies' },
  { key: 'php', name: 'PHP', judge0_id: 68, file_name: 'main.php', monaco_id: 'php', time_multiplier: 2.0, course: 'Web technologies' },
  { key: 'ruby', name: 'Ruby', judge0_id: 72, file_name: 'main.rb', monaco_id: 'ruby', time_multiplier: 3.0, course: 'Scripting' },
  // HTML pages are parsed by a Python harness (the html grader), so they use Python's Judge0 id.
  { key: 'html', name: 'HTML', judge0_id: 71, file_name: 'index.html', monaco_id: 'html', time_multiplier: 1.0, course: 'Web technologies (HTML grader)' },
  // SQL runs in a Python + SQLite harness (the sql grader).
  { key: 'sql', name: 'SQL (SQLite)', judge0_id: 82, file_name: 'query.sql', monaco_id: 'sql', time_multiplier: 2.0, course: 'DBMS (SQL grader)' },
  // Systems, OS, computer organisation
  { key: 'bash', name: 'Bash', judge0_id: 46, file_name: 'script.sh', monaco_id: 'shell', time_multiplier: 2.0, course: 'Operating systems, Linux lab' },
  { key: 'assembly', name: 'Assembly (NASM x86)', judge0_id: 45, file_name: 'main.asm', monaco_id: 'plaintext', time_multiplier: 1.0, course: 'Computer organisation, microprocessors' },
  // Data science, ML, AI
  { key: 'python3-ml', name: 'Python 3 (ML)', judge0_id: 71, file_name: 'main.py', monaco_id: 'python', time_multiplier: 3.0, course: 'Machine learning (needs numpy/pandas/scikit-learn in Judge0)' },
  { key: 'r', name: 'R', judge0_id: 80, file_name: 'main.r', monaco_id: 'r', time_multiplier: 3.0, course: 'Statistics, data science' },
  { key: 'octave', name: 'Octave (MATLAB-like)', judge0_id: 66, file_name: 'main.m', monaco_id: 'plaintext', time_multiplier: 3.0, course: 'Numerical methods, ML, signal processing' },
  { key: 'scala', name: 'Scala', judge0_id: 81, file_name: 'Main.scala', monaco_id: 'scala', time_multiplier: 2.5, course: 'Big data (Spark)' },
  { key: 'prolog', name: 'Prolog', judge0_id: 69, file_name: 'main.pl', monaco_id: 'plaintext', time_multiplier: 2.0, course: 'Artificial intelligence (logic)' },
  { key: 'commonlisp', name: 'Common Lisp', judge0_id: 55, file_name: 'script.lisp', monaco_id: 'plaintext', time_multiplier: 2.0, course: 'Artificial intelligence' },
  { key: 'haskell', name: 'Haskell', judge0_id: 61, file_name: 'main.hs', monaco_id: 'plaintext', time_multiplier: 1.5, course: 'Functional programming, theory of computation' },
  // Others still common in syllabi
  { key: 'swift', name: 'Swift', judge0_id: 83, file_name: 'main.swift', monaco_id: 'swift', time_multiplier: 1.5, course: 'iOS development' },
  { key: 'perl', name: 'Perl', judge0_id: 85, file_name: 'main.pl', monaco_id: 'perl', time_multiplier: 3.0, course: 'Scripting' },
  { key: 'lua', name: 'Lua', judge0_id: 64, file_name: 'main.lua', monaco_id: 'lua', time_multiplier: 2.0, course: 'Scripting' },
  { key: 'pascal', name: 'Pascal', judge0_id: 67, file_name: 'main.pas', monaco_id: 'pascal', time_multiplier: 1.0, course: 'Introductory programming' },
  { key: 'fortran', name: 'Fortran', judge0_id: 59, file_name: 'main.f90', monaco_id: 'plaintext', time_multiplier: 1.0, course: 'Numerical / scientific computing' },
]
