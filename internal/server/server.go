package server

import (
	"encoding/json"
	"log"
	"net/http"

	"github.com/gorilla/mux"
)

// VersionHandler handles requests to the /version endpoint
func VersionHandler(w http.ResponseWriter, r *http.Request) {
	version := "ARTEFACT_VERSION"
	json.NewEncoder(w).Encode(map[string]string{"version": version})
}

// SetupRoutes sets up the API routes
func SetupRoutes(router *mux.Router) {
	router.HandleFunc("/version", VersionHandler).Methods("GET")
}