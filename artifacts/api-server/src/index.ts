import { spawn } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
import app from "./app";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

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
const MAX_PYTHON_RETRIES = 5;
let pythonRetryCount = 0;
let activePyProcess: ReturnType<typeof spawn> | null = null;

function startPythonBackend() {
  if (process.env.NODE_ENV === "development") return;

  if (pythonRetryCount >= MAX_PYTHON_RETRIES) {
    console.error(
      `Python API server failed ${MAX_PYTHON_RETRIES} times. Giving up. ` +
      `Check if port ${PYTHON_API_PORT} is already in use (e.g. from start.bat).`,
    );
    return;
  }

  pythonRetryCount++;
  console.log(
    `Starting Python API server on port ${PYTHON_API_PORT}... (attempt ${pythonRetryCount}/${MAX_PYTHON_RETRIES})`,
  );

  const pyProcess = spawn(
    "python",
    [
      "-m",
      "uvicorn",
      "backend.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      PYTHON_API_PORT,
      "--timeout-keep-alive",
      "300",
      "--h11-max-incomplete-event-size",
      "0",
    ],
    {
      cwd: path.resolve(__dirname, "../../.."),
      env: { ...process.env, PYTHON_API_PORT },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  activePyProcess = pyProcess;

  pyProcess.stdout?.on("data", (data: Buffer) => {
    process.stdout.write(`[python] ${data}`);
    // Reset retry count only when uvicorn is actually serving
    if (data.toString().includes("Uvicorn running on")) {
      pythonRetryCount = 0;
    }
  });

  pyProcess.stderr?.on("data", (data: Buffer) => {
    process.stderr.write(`[python] ${data}`);
    if (data.toString().includes("Uvicorn running on")) {
      pythonRetryCount = 0;
    }
  });

  pyProcess.on("exit", (code) => {
    activePyProcess = null;
    console.error(`Python API server exited with code ${code}`);
    const delay = Math.min(2000 * Math.pow(2, pythonRetryCount - 1), 30000);
    setTimeout(() => {
      console.log("Restarting Python API server...");
      startPythonBackend();
    }, delay);
  });
}

// Register signal handlers once (outside the function to avoid listener leaks)
process.on("SIGTERM", () => {
  activePyProcess?.kill("SIGTERM");
});
process.on("SIGINT", () => {
  activePyProcess?.kill("SIGTERM");
});

startPythonBackend();

app.listen(port, () => {
  console.log(`Server listening on port ${port}`);
});
