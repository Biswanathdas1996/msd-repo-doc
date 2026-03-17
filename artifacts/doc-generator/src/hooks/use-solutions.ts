import { useQueryClient } from "@tanstack/react-query";
import {
  useListSolutions,
  useGetSolution,
  useUploadSolution,
  useImportFromGithub,
  useDeleteSolution,
  useGetKnowledgeGraph,
  useGetEntities,
  useGetWorkflows,
  useGetPlugins,
  useGetFunctionalFlows,
  useGenerateDocs,
  useGetDocs,
  useVerifyDocs,
} from "@workspace/api-client-react";

// Wrapper hooks to handle standard cache invalidation and provide a unified API surface

export function useSolutions() {
  return useListSolutions({
    query: {
      refetchInterval: 10000, // Poll every 10s for status updates
    }
  });
}

export function useSolutionDetails(id: string) {
  return useGetSolution(id, {
    query: {
      enabled: !!id,
      refetchInterval: (query) => {
        // Stop polling if status is ready or error
        const status = query.state.data?.status;
        if (status === 'ready' || status === 'error') return false;
        return 5000;
      }
    }
  });
}

export function useUpload() {
  const queryClient = useQueryClient();
  return useUploadSolution({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["/py-api/solutions"] });
      },
    },
  });
}

export function useGitHubImport() {
  const queryClient = useQueryClient();
  return useImportFromGithub({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["/py-api/solutions"] });
      },
    },
  });
}

export function useDelete() {
  const queryClient = useQueryClient();
  return useDeleteSolution({
    mutation: {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({ queryKey: ["/py-api/solutions"] });
        queryClient.removeQueries({ queryKey: [`/py-api/solutions/${variables.id}`] });
      },
    },
  });
}

export function useKnowledgeGraph(id: string) {
  return useGetKnowledgeGraph(id, { query: { enabled: !!id } });
}

export function useEntities(id: string) {
  return useGetEntities(id, { query: { enabled: !!id } });
}

export function useWorkflows(id: string) {
  return useGetWorkflows(id, { query: { enabled: !!id } });
}

export function usePlugins(id: string) {
  return useGetPlugins(id, { query: { enabled: !!id } });
}

export function useFunctionalFlows(id: string) {
  return useGetFunctionalFlows(id, { query: { enabled: !!id } });
}

export function useDocs(id: string) {
  return useGetDocs(id, { query: { enabled: !!id, retry: false } });
}

export function useGenerateDocumentation() {
  const queryClient = useQueryClient();
  return useGenerateDocs({
    mutation: {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({ queryKey: [`/py-api/solutions/${variables.id}/docs`] });
        queryClient.invalidateQueries({ queryKey: [`/py-api/solutions/${variables.id}`] });
        queryClient.invalidateQueries({ queryKey: ["/py-api/solutions"] });
      },
    },
  });
}

export function useVerifyDocumentation() {
  const queryClient = useQueryClient();
  return useVerifyDocs({
    mutation: {
      onSuccess: (_, variables) => {
        queryClient.invalidateQueries({ queryKey: [`/py-api/solutions/${variables.id}/docs`] });
      },
    },
  });
}
