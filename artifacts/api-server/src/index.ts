import { spawn } from "child_process";
import app from "./app";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

const PYTHON_API_PORT = process.env.PYTHON_API_PORT || "5001";

function startPythonBackend() {
  if (process.env.NODE_ENV === "development") return;

  console.log(`Starting Python API server on port ${PYTHON_API_PORT}...`);

  const pyProcess = spawn(
    "python",
    [
      "-m",
      "uvicorn",
      "backend.main:app",
      "--host",
      "0.0.0.0",
      "--port",
      PYTHON_API_PORT,
      "--timeout-keep-alive",
      "300",
      "--h11-max-incomplete-event-size",
      "0",
    ],
    {
      cwd: process.cwd(),
      env: { ...process.env, PYTHON_API_PORT },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  pyProcess.stdout?.on("data", (data: Buffer) => {
    process.stdout.write(`[python] ${data}`);
  });

  pyProcess.stderr?.on("data", (data: Buffer) => {
    process.stderr.write(`[python] ${data}`);
  });

  pyProcess.on("exit", (code) => {
    console.error(`Python API server exited with code ${code}`);
    setTimeout(() => {
      console.log("Restarting Python API server...");
      startPythonBackend();
    }, 2000);
  });

  process.on("SIGTERM", () => {
    pyProcess.kill("SIGTERM");
  });

  process.on("SIGINT", () => {
    pyProcess.kill("SIGTERM");
  });
}

startPythonBackend();

app.listen(port, () => {
  console.log(`Server listening on port ${port}`);
});
