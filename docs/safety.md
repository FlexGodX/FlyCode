# FlyCode Safety System

## Overview

Safety is the highest priority. The fly controller **never** bypasses safety checks.

## Safety Layer Architecture

```
Candidate Scores → Fly Bias → Risk Detector → Safety Mask → Final Selection
```

**Safety always wins over fly control.**