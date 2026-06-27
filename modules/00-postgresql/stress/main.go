// Command stress is a load generator for §A3 (hot-row contention).
//
// It hammers a table from many concurrent workers in two modes and reports
// throughput, so we can see that contention is a property of the *key
// distribution*, not the amount of load:
//
//	contended : every worker UPDATEs the SAME row  -> writers serialize, throughput flatlines
//	spread    : every worker UPDATEs a DIFFERENT row -> no serialization, throughput scales
//
// Each UPDATE runs in its own (autocommit) transaction, so every increment
// pays a commit + WAL fsync — the cost that sets the per-row throughput ceiling.
package main

import (
	"database/sql"
	"flag"
	"fmt"
	"log"
	"sync"
	"sync/atomic"
	"time"

	_ "github.com/lib/pq"
)

func main() {
	workerCount := flag.Int("workers", 16, "number of concurrent workers (and pooled connections)")
	phaseDuration := flag.Duration("duration", 5*time.Second, "how long to run each phase")
	mode := flag.String("mode", "both", "contended | spread | both")
	rowCount := flag.Int("rows", 0, "distinct rows available in spread mode (0 = one per worker)")
	dsn := flag.String("dsn",
		"host=localhost port=5433 user=study password=study dbname=study sslmode=disable",
		"PostgreSQL connection string")
	flag.Parse()

	if *rowCount == 0 {
		*rowCount = *workerCount
	}

	database, err := sql.Open("postgres", *dsn)
	if err != nil {
		log.Fatalf("open: %v", err)
	}
	defer database.Close()

	// One pooled connection per worker, so workers genuinely run in parallel.
	database.SetMaxOpenConns(*workerCount)
	database.SetMaxIdleConns(*workerCount)

	if err := database.Ping(); err != nil {
		log.Fatalf("cannot connect (is the pg-study container up on :5433?): %v", err)
	}

	setupTable(database, *rowCount)

	switch *mode {
	case "contended":
		runPhase(database, "CONTENDED — every worker UPDATEs row 1", *workerCount, *phaseDuration, true, *rowCount)
	case "spread":
		runPhase(database, "SPREAD — every worker UPDATEs its own row", *workerCount, *phaseDuration, false, *rowCount)
	case "both":
		contended := runPhase(database, "CONTENDED — every worker UPDATEs row 1", *workerCount, *phaseDuration, true, *rowCount)
		spread := runPhase(database, "SPREAD — every worker UPDATEs its own row", *workerCount, *phaseDuration, false, *rowCount)
		fmt.Printf("\nspread / contended speedup: %.1fx\n", spread/contended)
	default:
		log.Fatalf("unknown mode %q (use contended | spread | both)", *mode)
	}
}

// setupTable creates a fresh `hot` table seeded with rowCount counter rows.
func setupTable(database *sql.DB, rowCount int) {
	statements := []string{
		`DROP TABLE IF EXISTS hot`,
		`CREATE TABLE hot (id int PRIMARY KEY, counter bigint NOT NULL DEFAULT 0)`,
		fmt.Sprintf(`INSERT INTO hot (id) SELECT generate_series(1, %d)`, rowCount),
	}
	for _, statement := range statements {
		if _, err := database.Exec(statement); err != nil {
			log.Fatalf("setup failed on %q: %v", statement, err)
		}
	}
}

// runPhase launches workerCount goroutines that UPDATE rows as fast as they can
// until phaseDuration elapses, then prints and returns the throughput (updates/sec).
// When contended is true every worker targets row 1; otherwise each targets its own row.
func runPhase(database *sql.DB, label string, workerCount int, phaseDuration time.Duration, contended bool, rowCount int) float64 {
	if _, err := database.Exec(`UPDATE hot SET counter = 0`); err != nil {
		log.Fatalf("reset counters: %v", err)
	}

	var totalUpdates int64
	var waitGroup sync.WaitGroup
	deadline := time.Now().Add(phaseDuration)
	start := time.Now()

	for workerIndex := 0; workerIndex < workerCount; workerIndex++ {
		waitGroup.Add(1)
		go func(workerIndex int) {
			defer waitGroup.Done()

			targetRow := 1
			if !contended {
				targetRow = (workerIndex % rowCount) + 1
			}

			var localUpdates int64
			for time.Now().Before(deadline) {
				if _, err := database.Exec(`UPDATE hot SET counter = counter + 1 WHERE id = $1`, targetRow); err != nil {
					log.Printf("worker %d update error: %v", workerIndex, err)
					return
				}
				localUpdates++
			}
			atomic.AddInt64(&totalUpdates, localUpdates)
		}(workerIndex)
	}

	waitGroup.Wait()
	elapsed := time.Since(start)
	throughput := float64(totalUpdates) / elapsed.Seconds()

	fmt.Printf("\n=== %s ===\n", label)
	fmt.Printf("workers=%d  duration=%s  rows=%d\n", workerCount, phaseDuration, rowCount)
	fmt.Printf("total updates : %d\n", totalUpdates)
	fmt.Printf("throughput    : %.0f updates/sec\n", throughput)
	return throughput
}
