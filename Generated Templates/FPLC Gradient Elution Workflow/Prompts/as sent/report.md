# Report agent — prompt as sent

> The payload the report agent received for *ELN report body* in the replication study, identical across all 20 replicates apart from the one line noted below.
> The wizard prompt it was built from is `../report.md`.
>
> One line of this prompt is assembled per replicate from what the upstream analysis script produced; the variants observed were:
> - `1. Chromatogram {Id} (chromatogram_{ID}.png) - path_variable`
> - `1. Chromatogram {Id} (chromatogram_{ID}.png) - fstring_plot`
> - `1. Akta Chromatogram {Id} (akta_chromatogram_{ID}.png) - fstring_plot`
> - `1. Akta Chromatogram {Id} (akta_chromatogram_{ID}.png) - path_variable`

---

```text
WORKFLOW CONTEXT:
The following workflow describes the experimental process:
{
  "type": "steps",
  "content": [
    {
      "device": "Utility",
      "method": "Pause",
      "params": {
        "message": "Manually fill sample loop"
      }
    },
    {
      "device": "AKTA Pure",
      "method": "Run Protocol",
      "params": {
        "akta_phases": [],
        "sample_signals": [
          "uv1",
          "cond"
        ],
        "sample_time": 1
      }
    }
  ]
}

USER REQUEST:
Please create a report for my ÄKTA chromatography run. Include the utilized run parameters, the available placeholders, and the plots.
Structure:
1.	Heading
2.	Start & End-Date
3.	Methods (Describe the automated workflow)
4.	Results (Plots)

SELECTED PLOTS TO INCLUDE:
1. Chromatogram {Id} (chromatogram_{ID}.png) - path_variable

Please create specific sections in the HTML template for each selected plot. Use placeholders like [[PLOT_1]], [[PLOT_2]], etc. for the plot images. Include descriptive headings and proper styling for each plot section. IMPORTANT: The plots will be uploaded to eLabFTW and need proper download URLs.
```
