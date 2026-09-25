//! Versioned, request-local fixed binding. Unlisted cells are unconstrained.
use crate::compact::CompactState;
use crate::{ContractError, ContractResult, Reader};
use sha2::{Digest, Sha256};
use std::time::Instant;

pub(crate) const TAG: u16 = 0x8007;
pub(crate) const RESULT_TAG: u16 = 0x8307;

#[derive(Clone)]
pub(crate) struct SelectedTemplate {
    pub(crate) digest: [u8; 32],
    required: [u128; 5],
    forbidden: [u128; 6],
}
impl SelectedTemplate {
    pub(crate) fn parse(data: &[u8]) -> ContractResult<Self> {
        let mut r = Reader::new(data, TAG);
        if r.u16("template ABI")? != 1
            || r.string("template schema")? != "puyo.selected_template.v1"
        {
            return Err(ContractError::incompatible(
                TAG,
                "unsupported selected-template schema/ABI",
            ));
        }
        for _ in 0..3 {
            if r.string("template identity")?.is_empty() {
                return Err(ContractError::invalid(TAG, "empty template identity"));
            }
        }
        let count = r.u8("binding count")?;
        if count == 0 || count > 5 {
            return Err(ContractError::invalid(TAG, "invalid binding count"));
        }
        let mut colors = 0_u8;
        let mut last_label = String::new();
        for _ in 0..count {
            let label = r.string("binding label")?;
            let color = r.u8("binding color")?;
            if label <= last_label || !(1..=5).contains(&color) || colors & (1 << color) != 0 {
                return Err(ContractError::invalid(
                    TAG,
                    "invalid canonical fixed binding",
                ));
            }
            last_label = label;
            colors |= 1 << color;
        }
        let mut required = [0_u128; 5];
        let mut forbidden = [0_u128; 6];
        let mut required_mask = 0_u128;
        for kind in 0..2 {
            let count = r.u16("cell count")?;
            if count > 504 || (kind == 0 && count == 0) {
                return Err(ContractError::invalid(TAG, "invalid template cell count"));
            }
            let mut previous = None;
            for _ in 0..count {
                let x = r.u8("cell x")?;
                let y = r.u8("cell y")?;
                let color = r.u8("cell color")?;
                if x >= 6
                    || y >= 14
                    || color > 5
                    || (color == 0 && kind == 0)
                    || (color != 0 && colors & (1 << color) == 0)
                    || previous.is_some_and(|p| p >= (x, y, color))
                {
                    return Err(ContractError::invalid(
                        TAG,
                        "invalid canonical template cell",
                    ));
                }
                previous = Some((x, y, color));
                let bit = 1_u128 << (y * 6 + x);
                if kind == 0 {
                    if required_mask & bit != 0 {
                        return Err(ContractError::invalid(TAG, "duplicate required coordinate"));
                    }
                    required_mask |= bit;
                    required[usize::from(color - 1)] |= bit;
                } else {
                    if (color == 0 && required_mask & bit != 0)
                        || (color > 0 && required[usize::from(color - 1)] & bit != 0)
                    {
                        return Err(ContractError::invalid(TAG, "contradictory template cells"));
                    }
                    forbidden[usize::from(color)] |= bit;
                }
            }
        }
        r.finish()?;
        Ok(Self {
            digest: Sha256::digest(data).into(),
            required,
            forbidden,
        })
    }

    pub(crate) fn evaluate(&self, state: &CompactState, parent: &CompactState) -> (bool, bool) {
        let planes = state.wire_planes();
        let parent_planes = parent.wire_planes();
        let occupied = state.occupied();
        let mut complete = true;
        for i in 0..5 {
            let missing = self.required[i] & !planes[i];
            if missing != 0 {
                complete = false;
            }
            if missing & (occupied | parent_planes[i]) != 0
                || planes[i] & self.forbidden[i + 1] != 0
            {
                return (false, false);
            }
        }
        if occupied & self.forbidden[0] != 0 {
            return (false, false);
        }
        (true, complete)
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct Record {
    pub(crate) checks: u64,
    pub(crate) check_ns: u64,
    pub(crate) rejected: u64,
    pub(crate) root_violation: bool,
    pub(crate) known: Vec<u8>,
    pub(crate) sampled: Vec<u8>,
    pub(crate) sampled_scenario: Option<u8>,
}
impl Record {
    pub(crate) fn check(
        &mut self,
        template: &SelectedTemplate,
        state: &CompactState,
        parent: &CompactState,
        path: &[u8],
        known_count: usize,
        initial_valid: bool,
        game_over: bool,
        scenario_id: u8,
    ) -> bool {
        self.checks += 1;
        let started = Instant::now();
        let (valid, complete) = template.evaluate(state, parent);
        self.check_ns += started.elapsed().as_nanos().min(u128::from(u64::MAX)) as u64;
        if !initial_valid || !valid {
            self.rejected += 1;
            self.root_violation |= path.len() == 1;
            return false;
        }
        if complete && !game_over {
            let witness = if path.len() <= known_count {
                &mut self.known
            } else {
                &mut self.sampled
            };
            if witness.is_empty() || (path.len(), path) < (witness.len(), witness.as_slice()) {
                *witness = path.to_vec();
                if path.len() > known_count {
                    self.sampled_scenario = Some(scenario_id);
                }
            }
        }
        true
    }
    pub(crate) fn add(&mut self, other: &Self) {
        self.checks += other.checks;
        self.check_ns += other.check_ns;
        self.rejected += other.rejected;
        self.root_violation |= other.root_violation;
        if !other.known.is_empty()
            && (self.known.is_empty()
                || (other.known.len(), &other.known) < (self.known.len(), &self.known))
        {
            self.known = other.known.clone();
        }
        if !other.sampled.is_empty()
            && (self.sampled.is_empty()
                || (other.sampled.len(), &other.sampled, other.sampled_scenario)
                    < (self.sampled.len(), &self.sampled, self.sampled_scenario))
        {
            self.sampled = other.sampled.clone();
            self.sampled_scenario = other.sampled_scenario;
        }
    }
}
