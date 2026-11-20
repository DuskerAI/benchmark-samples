extern void optional_hook(void) __attribute__((weak));
int beta_open(void) { if (optional_hook) optional_hook(); return 3; }
int beta_trace_marker(void) { return 5; }
